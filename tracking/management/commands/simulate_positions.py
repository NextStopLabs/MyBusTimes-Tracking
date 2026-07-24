from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, F
from django.core.cache import cache
from datetime import timedelta
from tracking.models import Trip
from fleet.models import fleet
from routes.models import routeStop
from tracking.utils import calculate_heading
import time
import json

CACHE_KEY = "route_coords_cache"
CACHE_TIMEOUT = 3600


class Command(BaseCommand):
    help = "Simulate vehicle positions for all active trips"

    def handle(self, *args, **kwargs):
        t0 = time.monotonic()
        now = timezone.now()
        two_mins_ago = now - timedelta(minutes=2)
        eight_hours_ago = now - timedelta(hours=8)

        vehicle_trips = self._get_vehicle_trips(now, two_mins_ago, eight_hours_ago)
        self.stdout.write(
            f"Query took {time.monotonic() - t0:.2f}s, found {len(vehicle_trips)} vehicles"
        )

        if not vehicle_trips:
            self.clear_old_positions(now)
            return

        self.clear_old_positions(now)

        t1 = time.monotonic()
        route_coords = self._get_route_coords(vehicle_trips)
        self.stdout.write(
            f"Route coords in {time.monotonic() - t1:.2f}s"
        )

        t2 = time.monotonic()
        updates = self._compute_positions(vehicle_trips, route_coords, now, two_mins_ago)
        self.stdout.write(f"Processing took {time.monotonic() - t2:.2f}s")

        if updates:
            t3 = time.monotonic()
            fleet.objects.bulk_update(
                updates,
                ["sim_lat", "sim_lon", "sim_heading", "current_trip", "updated_at"],
                batch_size=500,
            )
            self.stdout.write(
                f"Updated {len(updates)} vehicles in {time.monotonic() - t3:.2f}s"
            )

        self.stdout.write(f"Total time: {time.monotonic() - t0:.2f}s")

    def _get_vehicle_trips(self, now, two_mins_ago, eight_hours_ago):
        rows = (
            Trip.objects.filter(trip_missed=False, trip_start_at__lte=now)
            .filter(
                Q(trip_end_at__gte=two_mins_ago)
                | Q(
                    trip_end_at__lt=F("trip_start_at"),
                    trip_start_at__gte=eight_hours_ago,
                )
            )
            .values(
                "trip_id",
                "trip_vehicle_id",
                "trip_route_id",
                "trip_start_at",
                "trip_end_at",
                "trip_inbound",
                "trip_end_location",
            )
            .order_by("trip_vehicle_id", "-trip_start_at")
        )

        vehicle_trips = {}
        for row in rows:
            vid = row["trip_vehicle_id"]
            if vid not in vehicle_trips:
                vehicle_trips[vid] = row

        return vehicle_trips

    def clear_old_positions(self, now):
        fifteen_mins_ago = now - timedelta(minutes=15)
        eight_hours_ago = now - timedelta(hours=8)

        ended_trip_ids = Trip.objects.filter(
            Q(trip_end_at__gte=F("trip_start_at"), trip_end_at__lt=fifteen_mins_ago)
            | Q(
                trip_end_at__lt=F("trip_start_at"),
                trip_start_at__lt=eight_hours_ago,
            )
        ).values_list("pk", flat=True)

        updated_count = fleet.objects.filter(
            current_trip_id__in=ended_trip_ids
        ).update(
            sim_lat=None,
            sim_lon=None,
            sim_heading=None,
            current_trip=None,
            updated_at=None,
        )

        if updated_count:
            self.stdout.write(f"Cleared {updated_count} old positions.")

    def _get_route_coords(self, vehicle_trips):
        coords_cache = cache.get(CACHE_KEY) or {}
        route_ids = {
            t["trip_route_id"]
            for t in vehicle_trips.values()
            if t["trip_route_id"]
        }
        missing = [rid for rid in route_ids if rid not in coords_cache]

        if missing:
            self._fetch_route_coords(missing, coords_cache)

        return coords_cache

    def _fetch_route_coords(self, route_ids, coords_cache):
        batch_size = 30
        for i in range(0, len(route_ids), batch_size):
            batch = route_ids[i : i + batch_size]
            rows = (
                routeStop.objects.filter(route_id__in=batch)
                .values_list("route_id", "inbound", "stops", "snapped_route")
                .order_by("route_id", "id")
            )

            groups = {}
            for row in rows:
                route_id = row[0]
                groups.setdefault(route_id, []).append(row)

            for route_id, stops_data in groups.items():
                coords_cache[route_id] = self._parse_route_coords(stops_data)

        cache.set(CACHE_KEY, coords_cache, CACHE_TIMEOUT)

    def _parse_route_coords(self, stops_data):
        result = {"inbound": None, "outbound": None, "directions": []}

        for i, (route_id, inbound, stops, snapped_route) in enumerate(stops_data):
            snapped_coords = self._parse_snapped(snapped_route)

            if snapped_coords:
                coords = snapped_coords
                last_stop = ""
            elif isinstance(stops, list):
                coords, last_stop = self._parse_stops(stops)
            else:
                continue

            if not coords:
                continue

            if i == 0:
                result["outbound"] = coords
            elif i == 1:
                result["inbound"] = coords

            result["directions"].append(
                {
                    "coords": coords,
                    "last_stop": (last_stop or "").lower().strip(),
                }
            )

        return result

    @staticmethod
    def _parse_snapped(snapped_route):
        if not snapped_route:
            return None
        try:
            data = json.loads(snapped_route)
            coords = [(float(p[1]), float(p[0])) for p in data if isinstance(p, (list, tuple)) and len(p) == 2]
            return coords if coords else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    @staticmethod
    def _parse_stops(stops):
        coords = []
        last_stop_name = ""
        for stop in stops:
            if not isinstance(stop, dict):
                continue
            sname = stop.get("stop") or stop.get("name") or stop.get("title")
            if sname:
                last_stop_name = str(sname)
            cords = stop.get("cords") or stop.get("coords")
            if cords:
                try:
                    lat_str, lng_str = cords.split(",")
                    coords.append((float(lat_str.strip()), float(lng_str.strip())))
                    continue
                except (ValueError, AttributeError):
                    pass
            lat = stop.get("lat") or stop.get("latitude")
            lng = stop.get("lng") or stop.get("longitude") or stop.get("long")
            if lat is not None and lng is not None:
                try:
                    coords.append((float(lat), float(lng)))
                except (ValueError, TypeError):
                    pass
        return coords, last_stop_name

    def _compute_positions(self, vehicle_trips, route_coords, now, two_mins_ago):
        updates = []

        for vid, trip in vehicle_trips.items():
            route_id = trip["trip_route_id"]
            if not route_id:
                continue

            route_data = route_coords.get(route_id)
            coords = self._select_coords(route_data, trip)
            if not coords:
                continue

            start = trip["trip_start_at"]
            end = trip["trip_end_at"]
            if not start or not end:
                continue

            if not (end < start) and end < two_mins_ago:
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
                heading = 0.0
            else:
                total_segments = len(coords) - 1
                if total_segments <= 0:
                    lat, lng = coords[0]
                    heading = 0.0
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
                        lat2, lng2 = (
                            coords[seg_index - 1] if seg_index > 0 else coords[0]
                        )
                    else:
                        lat2, lng2 = coords[seg_index + 1]

                    heading = calculate_heading(lat, lng, lat2, lng2)

            updates.append(
                fleet(
                    id=vid,
                    sim_lat=lat,
                    sim_lon=lng,
                    sim_heading=heading,
                    current_trip_id=trip["trip_id"],
                    updated_at=now,
                )
            )

        return updates

    @staticmethod
    def _select_coords(route_data, trip):
        if not route_data:
            return None

        if trip["trip_inbound"] is False:
            return route_data.get("inbound") or route_data.get("outbound")

        if trip["trip_inbound"] is True:
            return route_data.get("outbound")

        trip_end = (trip.get("trip_end_location") or "").lower().strip()

        for d in route_data.get("directions", []):
            if d["coords"]:
                if not trip_end:
                    return d["coords"]
                if d["last_stop"] and trip_end in d["last_stop"]:
                    return d["coords"]

        return route_data.get("outbound") or route_data.get("inbound")
