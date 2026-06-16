from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, F
from django.core.cache import cache
from datetime import timedelta
from tracking.models import Trip
from fleet.models import fleet
from django.db import IntegrityError, OperationalError, transaction
from routes.models import routeStop
import time

from tracking.utils import (
    calculate_heading,
    extract_coords_from_routeStop,
    extract_coords_and_last_stop,
    extract_stops_data,
)

CACHE_KEY = "route_coords_cache"
CACHE_TIMEOUT = 3600


class Command(BaseCommand):
    help = "Simulate vehicle positions for all active trips"

    def handle(self, *args, **kwargs):
        t0 = time.time()
        now = timezone.now()

        two_mins_ago = now - timedelta(minutes=2)
        eight_hours_ago = now - timedelta(hours=8)

        normal_trips = Trip.objects.filter(
            trip_missed=False,
            trip_start_at__lte=now,
            trip_end_at__gte=two_mins_ago,
        ).select_related("trip_vehicle", "trip_route")

        midnight_trips = Trip.objects.filter(
            trip_missed=False,
            trip_start_at__lte=now,
            trip_start_at__gte=eight_hours_ago,
            trip_end_at__lt=F('trip_start_at'),
        ).select_related("trip_vehicle", "trip_route")

        active_trips = {t.trip_id: t for t in normal_trips}
        for t in midnight_trips:
            if t.trip_id not in active_trips:
                active_trips[t.trip_id] = t

        active_trips = list(active_trips.values())

        self.stdout.write(f"Query took {time.time() - t0:.2f}s, found {len(active_trips)} trips")

        if not active_trips:
            self.stdout.write("No active trips found.")
            self.clear_old_positions(now)
            return

        self.clear_old_positions(now)

        t1 = time.time()
        route_ids = {t.trip_route_id for t in active_trips if t.trip_route_id}

        coords_cache = cache.get(CACHE_KEY) or {}

        routes_to_fetch = {rid for rid in route_ids if rid not in coords_cache}
        cached_count = len(route_ids) - len(routes_to_fetch)

        if routes_to_fetch:
            route_stops_qs = (
                routeStop.objects
                .filter(route_id__in=routes_to_fetch)
                .only("id", "route_id", "inbound", "stops", "snapped_route")
                .order_by("route_id", "id")
            )

            route_stops_map = {}
            for rs in route_stops_qs.iterator(chunk_size=500):
                route_stops_map.setdefault(rs.route_id, []).append(rs)

            for route_id, stops_list in route_stops_map.items():
                coords_cache[route_id] = self._parse_route_coords(stops_list)

            cache.set(CACHE_KEY, coords_cache, CACHE_TIMEOUT)

            self.stdout.write(f"Fetched {len(routes_to_fetch)} routes in {time.time() - t1:.2f}s (cached: {cached_count})")
        else:
            self.stdout.write(f"All {len(route_ids)} routes from cache ({time.time() - t1:.3f}s)")

        t2 = time.time()
        vehicles_to_update = []
        seen_vehicles = set()

        for trip in active_trips:
            try:
                vehicle = trip.trip_vehicle
                if not vehicle or vehicle.id in seen_vehicles:
                    continue

                start = trip.trip_start_at
                end = trip.trip_end_at

                if not start:
                    continue

                route_data = coords_cache.get(trip.trip_route_id)
                if not route_data:
                    continue

                # Try stop-based positioning (with arrival/departure times)
                stops = self._get_stops_for_trip(route_data, trip)
                if stops:
                    lat, lng, heading = self._calculate_stop_position(stops, trip, now)
                    if lat is not None:
                        vehicle.sim_lat = lat
                        vehicle.sim_lon = lng
                        vehicle.sim_heading = heading
                        vehicle.current_trip = trip
                        vehicle.updated_at = now
                        vehicles_to_update.append(vehicle)
                        seen_vehicles.add(vehicle.id)
                        continue

                # Fall back to coordinate-based interpolation
                if not end:
                    continue

                is_midnight_crossing = end < start

                if not is_midnight_crossing and end < two_mins_ago:
                    continue

                coords = self._get_coords_for_trip(route_data, trip)
                if not coords:
                    continue

                duration = (end - start).total_seconds()
                if duration <= 0:
                    duration += 86400

                elapsed = (now - start).total_seconds()

                if elapsed <= 0:
                    progress = 0.0
                elif elapsed >= duration:
                    progress = 1.0
                else:
                    progress = elapsed / duration

                if progress >= 1.0 and elapsed > duration + 120:
                    continue

                if progress >= 1.0:
                    lat, lng = coords[-1]
                    heading = vehicle.sim_heading or 0
                else:
                    total_segments = len(coords) - 1
                    if total_segments <= 0:
                        lat, lng = coords[0]
                        heading = 0
                    else:
                        segment_float = progress * total_segments
                        seg_index = int(segment_float)

                        if seg_index >= total_segments:
                            lat, lng = coords[-1]
                            seg_index = total_segments - 1
                        else:
                            seg_progress = segment_float - seg_index
                            lat1, lng1 = coords[seg_index]
                            lat2, lng2 = coords[seg_index + 1]
                            lat = lat1 + (lat2 - lat1) * seg_progress
                            lng = lng1 + (lng2 - lng1) * seg_progress

                        if seg_index >= len(coords) - 1:
                            lat2, lng2 = coords[seg_index - 1] if seg_index > 0 else coords[0]
                        else:
                            lat2, lng2 = coords[seg_index + 1]

                        heading = calculate_heading(lat, lng, lat2, lng2)

                vehicle.sim_lat = lat
                vehicle.sim_lon = lng
                vehicle.sim_heading = heading
                vehicle.current_trip = trip
                vehicle.updated_at = now
                vehicles_to_update.append(vehicle)
                seen_vehicles.add(vehicle.id)
            except Exception as e:
                self.stderr.write(f"Error processing trip {trip.trip_id}: {e}")

        self.stdout.write(f"Processing took {time.time() - t2:.2f}s")

        if vehicles_to_update:
            t3 = time.time()
            vehicles_by_id = {v.id: v for v in vehicles_to_update}
            vehicle_ids = sorted(vehicles_by_id.keys())
            attempts = 0

            while True:
                try:
                    with transaction.atomic():
                        locked = list(
                            fleet.objects.select_for_update(skip_locked=True)
                            .filter(pk__in=vehicle_ids)
                            .order_by("pk")
                        )

                        if not locked:
                            self.stdout.write("No vehicles available for update; skipping.")
                            break

                        for v in locked:
                            src = vehicles_by_id.get(v.id)
                            if not src:
                                continue
                            v.sim_lat = src.sim_lat
                            v.sim_lon = src.sim_lon
                            v.sim_heading = src.sim_heading
                            v.current_trip = src.current_trip
                            v.updated_at = src.updated_at

                        try:
                            fleet.objects.bulk_update(
                                locked,
                                ["sim_lat", "sim_lon", "sim_heading", "current_trip", "updated_at"],
                                batch_size=500
                            )
                            self.stdout.write(
                                f"Updated {len(locked)} vehicles in {time.time() - t3:.2f}s"
                            )
                        except IntegrityError as e:
                            self.stderr.write(
                                f"Bulk update IntegrityError: {e}. Falling back to per-vehicle updates."
                            )
                            updated = 0
                            for v in locked:
                                try:
                                    v.save(update_fields=[
                                        "sim_lat", "sim_lon", "sim_heading",
                                        "current_trip", "updated_at",
                                    ])
                                    updated += 1
                                except IntegrityError as e2:
                                    self.stderr.write(
                                        f"Skipping vehicle {v.id} due to IntegrityError: {e2}"
                                    )
                            self.stdout.write(
                                f"Fallback updated {updated} vehicles in {time.time() - t3:.2f}s"
                            )
                    break
                except OperationalError as e:
                    if "deadlock detected" in str(e).lower() and attempts < 2:
                        attempts += 1
                        time.sleep(0.2 * attempts)
                        continue
                    raise

        self.stdout.write(f"Total time: {time.time() - t0:.2f}s")

    def _parse_route_coords(self, stops_list):
        result = {
            'inbound': None,
            'outbound': None,
            'directions': []
        }

        for i, rs in enumerate(stops_list):
            coords = extract_coords_from_routeStop(rs)
            stops_data = extract_stops_data(rs)

            if not coords and not stops_data:
                continue

            direction_data = {'coords': coords or []}

            if stops_data:
                direction_data['stops'] = stops_data
                last_stop_name = stops_data[-1]['name'] if stops_data else None
            else:
                _, last_stop_name = extract_coords_and_last_stop(rs)

            label = 'inbound' if rs.inbound else 'outbound'
            result[label] = direction_data

            result['directions'].append({
                **direction_data,
                'last_stop': (last_stop_name or "").lower().strip()
            })

        return result

    def _get_coords_for_trip(self, route_data, trip):
        if trip.trip_inbound is False:
            entry = route_data.get('outbound') or route_data.get('inbound') or {}
            coords = entry.get('coords') if isinstance(entry, dict) else entry
            return coords if isinstance(coords, list) else []

        if trip.trip_inbound is True:
            entry = route_data.get('inbound') or route_data.get('outbound') or {}
            coords = entry.get('coords') if isinstance(entry, dict) else entry
            return coords if isinstance(coords, list) else []

        trip_end = (trip.trip_end_location or "").lower().strip()

        for d in route_data.get('directions', []):
            coords = d.get('coords') or d
            if isinstance(coords, list) and coords:
                if not trip_end:
                    return coords
                if d.get('last_stop') and trip_end in d['last_stop']:
                    return coords

        entry = route_data.get('outbound') or route_data.get('inbound') or {}
        coords = entry.get('coords') if isinstance(entry, dict) else entry
        return coords if isinstance(coords, list) else []

    def _get_stops_for_trip(self, route_data, trip):
        """Get structured stops list for this trip's direction, or None if unavailable."""
        entry = None
        if trip.trip_inbound is False:
            entry = route_data.get('outbound') or route_data.get('inbound') or {}
        elif trip.trip_inbound is True:
            entry = route_data.get('inbound') or route_data.get('outbound') or {}
        else:
            trip_end = (trip.trip_end_location or "").lower().strip()
            for d in route_data.get('directions', []):
                if d.get('stops'):
                    ls = (d.get('last_stop') or "").lower().strip()
                    if not trip_end:
                        entry = d
                        break
                    if ls and trip_end in ls:
                        entry = d
                        break
            if not entry:
                entry = route_data.get('outbound') or route_data.get('inbound') or {}

        if isinstance(entry, dict):
            return entry.get('stops')
        return None

    def _resolve_schedule_index(self, trip, stops):
        """Determine which schedule column (trip_idx) this trip belongs to
        by matching the trip's start time against the first stop's departure_times/times."""
        if not stops or not trip.trip_start_at:
            return 0

        trip_time = trip.trip_start_at
        trip_minutes = trip_time.hour * 60 + trip_time.minute

        first_stop = stops[0]
        schedule_times = (first_stop.get('departure_times')
                          or first_stop.get('times') or [])

        best_idx = 0
        best_diff = 9999
        for i, t_str in enumerate(schedule_times):
            if not t_str:
                continue
            parts = t_str.split(':')
            stop_minutes = int(parts[0]) * 60 + int(parts[1])
            diff = abs(trip_minutes - stop_minutes)
            diff = min(diff, abs(trip_minutes - (stop_minutes + 1440)))
            diff = min(diff, abs((trip_minutes + 1440) - stop_minutes))
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        return best_idx

    def _get_active_stops(self, stops, schedule_idx):
        """Filter stops to only those served on this schedule (part-route handling).
        Stops with empty times[schedule_idx] mark the route endpoint."""
        if not stops:
            return []

        active = []
        for stop in stops:
            t = (stop.get('times') or stop.get('departure_times') or [])
            time_str = t[schedule_idx] if schedule_idx < len(t) else None
            if time_str:
                active.append(stop)
            else:
                break
        return active

    def _calculate_stop_position(self, stops, trip, now):
        """Calculate vehicle position using per-stop arrival/departure times.
        Returns (lat, lng, heading) or (None, None, None) if calculation fails."""

        schedule_idx = self._resolve_schedule_index(trip, stops)
        active_stops = self._get_active_stops(stops, schedule_idx)

        if len(active_stops) < 2:
            return None, None, None

        # Build timed schedule: list of (minutes_from_ref, lat, lng)
        schedule = []
        for i, stop in enumerate(active_stops):
            # Prefer departure_times for non-last stops, arrival_times for last
            if i < len(active_stops) - 1:
                times_arr = (stop.get('departure_times')
                             or stop.get('times') or [])
            else:
                times_arr = (stop.get('arrival_times')
                             or stop.get('times') or [])

            time_str = times_arr[schedule_idx] if schedule_idx < len(times_arr) else None
            if not time_str:
                times_arr = stop.get('times') or []
                time_str = times_arr[schedule_idx] if schedule_idx < len(times_arr) else None
            if not time_str:
                continue

            parts = time_str.split(':')
            minutes = int(parts[0]) * 60 + int(parts[1])

            if i > 0 and minutes < schedule[-1][0]:
                minutes += 1440

            lat = stop.get('lat')
            lng = stop.get('lng')
            if lat is None or lng is None:
                continue

            schedule.append((minutes, lat, lng))

        if len(schedule) < 2:
            return None, None, None

        # Current time relative to trip reference
        trip_time = trip.trip_start_at
        now_minutes = now.hour * 60 + now.minute
        ref_minutes = trip_time.hour * 60 + trip_time.minute

        if now_minutes < ref_minutes:
            now_minutes += 1440

        elapsed = now_minutes - ref_minutes

        ref_base = schedule[0][0]
        current_minutes = ref_base + elapsed

        if current_minutes <= schedule[0][0]:
            return schedule[0][1], schedule[0][2], 0.0

        if current_minutes >= schedule[-1][0]:
            return schedule[-1][1], schedule[-1][2], 0.0

        for i in range(len(schedule) - 1):
            t1 = schedule[i][0]
            t2 = schedule[i + 1][0]
            if t1 <= current_minutes <= t2:
                seg_progress = 0.0 if t2 == t1 else (current_minutes - t1) / (t2 - t1)
                lat1, lng1 = schedule[i][1], schedule[i][2]
                lat2, lng2 = schedule[i + 1][1], schedule[i + 1][2]
                lat = lat1 + (lat2 - lat1) * seg_progress
                lng = lng1 + (lng2 - lng1) * seg_progress
                heading = calculate_heading(lat, lng, lat2, lng2)
                return lat, lng, heading

        return schedule[-1][1], schedule[-1][2], 0.0

    def clear_old_positions(self, now):
        fifteen_mins_ago = now - timedelta(minutes=15)
        eight_hours_ago = now - timedelta(hours=8)

        updated_count = fleet.objects.filter(
            current_trip__isnull=False
        ).filter(
            Q(current_trip__trip_end_at__gte=F('current_trip__trip_start_at'),
              current_trip__trip_end_at__lt=fifteen_mins_ago) |
            Q(current_trip__trip_end_at__lt=F('current_trip__trip_start_at'),
              current_trip__trip_start_at__lt=eight_hours_ago)
        ).update(
            sim_lat=None,
            sim_lon=None,
            sim_heading=None,
            current_trip=None,
            updated_at=None
        )
        if updated_count > 0:
            self.stdout.write(f"Cleared {updated_count} old positions.")
