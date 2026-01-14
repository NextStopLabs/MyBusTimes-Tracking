from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, F
from django.core.cache import cache
from datetime import timedelta
from tracking.models import Trip
from fleet.models import fleet
from django.db import IntegrityError
from routes.models import routeStop
import time

# Import your existing helper functions
from tracking.utils import (
    calculate_heading,
    extract_coords_from_routeStop,
    extract_coords_and_last_stop,
)

CACHE_KEY = "route_coords_cache"
CACHE_TIMEOUT = 3600  # 1 hour

class Command(BaseCommand):
    help = "Simulate vehicle positions for all active trips"

    def handle(self, *args, **kwargs):
        t0 = time.time()
        now = timezone.now()
        
        # Pre-calculate time boundaries once
        two_mins_ago = now - timedelta(minutes=2)
        eight_hours_ago = now - timedelta(hours=8)

        # ---------------------------------------------------------
        # 1. Get active trips - optimized query
        # ---------------------------------------------------------
        
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

        # ---------------------------------------------------------
        # 2. Clear old positions
        # ---------------------------------------------------------
        self.clear_old_positions(now)

        # ---------------------------------------------------------
        # 3. Get route coordinates - with Django cache
        # ---------------------------------------------------------
        t1 = time.time()
        route_ids = {t.trip_route_id for t in active_trips if t.trip_route_id}
        
        # Load existing cache
        coords_cache = cache.get(CACHE_KEY) or {}
        
        # Check which routes we need to fetch
        routes_to_fetch = {rid for rid in route_ids if rid not in coords_cache}
        cached_count = len(route_ids) - len(routes_to_fetch)
        
        if routes_to_fetch:
            route_stops_qs = (
                routeStop.objects
                .filter(route_id__in=routes_to_fetch)
                .only("id", "route_id", "inbound", "stops", "snapped_route")
                .order_by("route_id", "id")
            )
            
            # Group by route
            route_stops_map = {}
            for rs in route_stops_qs.iterator(chunk_size=500):
                route_stops_map.setdefault(rs.route_id, []).append(rs)
            
            # Parse and add to cache
            for route_id, stops_list in route_stops_map.items():
                coords_cache[route_id] = self._parse_route_coords(stops_list)
            
            # Save updated cache
            cache.set(CACHE_KEY, coords_cache, CACHE_TIMEOUT)
            
            self.stdout.write(f"Fetched {len(routes_to_fetch)} routes in {time.time() - t1:.2f}s (cached: {cached_count})")
        else:
            self.stdout.write(f"All {len(route_ids)} routes from cache ({time.time() - t1:.3f}s)")

        # ---------------------------------------------------------
        # 4. Process trips and build update list
        # ---------------------------------------------------------
        t2 = time.time()
        vehicles_to_update = []
        seen_vehicles = set()
        
        for trip in active_trips:
            vehicle = trip.trip_vehicle
            if not vehicle or vehicle.id in seen_vehicles:
                continue

            start = trip.trip_start_at
            end = trip.trip_end_at
            
            if not start or not end:
                continue
                
            is_midnight_crossing = end < start
            
            if not is_midnight_crossing and end < two_mins_ago:
                continue

            # Get cached coordinates
            route_data = coords_cache.get(trip.trip_route_id)
            if not route_data:
                continue
            
            coords = self._get_coords_for_trip(route_data, trip)
            if not coords:
                continue

            # Calculate progress inline
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

            # Calculate position
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

        self.stdout.write(f"Processing took {time.time() - t2:.2f}s")

        # ---------------------------------------------------------
        # 5. Bulk update all vehicles at once
        # ---------------------------------------------------------
        if vehicles_to_update:
            t3 = time.time()
            try:
                fleet.objects.bulk_update(
                    vehicles_to_update,
                    ["sim_lat", "sim_lon", "sim_heading", "current_trip", "updated_at"],
                    batch_size=500
                )
                self.stdout.write(f"Updated {len(vehicles_to_update)} vehicles in {time.time() - t3:.2f}s")
            except IntegrityError as e:
                # Bulk update failed due to FK integrity (race or missing trip). Fall back
                # to per-vehicle save so we can skip problematic updates.
                self.stderr.write(f"Bulk update IntegrityError: {e}. Falling back to per-vehicle updates.")
                updated = 0
                for v in vehicles_to_update:
                    try:
                        v.save(update_fields=["sim_lat", "sim_lon", "sim_heading", "current_trip", "updated_at"])
                        updated += 1
                    except IntegrityError as e2:
                        self.stderr.write(f"Skipping vehicle {v.id} due to IntegrityError: {e2}")
                self.stdout.write(f"Fallback updated {updated} vehicles in {time.time() - t3:.2f}s")
        
        self.stdout.write(f"Total time: {time.time() - t0:.2f}s")

    def _parse_route_coords(self, stops_list):
        """Pre-parse all coordinate variants for a route."""
        result = {
            'inbound': None,
            'outbound': None,
            'directions': []
        }
        
        for i, rs in enumerate(stops_list):
            coords = extract_coords_from_routeStop(rs)
            if not coords:
                continue
            
            if i == 0:
                result['outbound'] = coords
            elif i == 1:
                result['inbound'] = coords
            
            _, last_stop = extract_coords_and_last_stop(rs)
            result['directions'].append({
                'coords': coords,
                'last_stop': (last_stop or "").lower().strip()
            })
        
        return result

    def _get_coords_for_trip(self, route_data, trip):
        """Get the right coordinates for a specific trip."""
        if trip.trip_inbound is False:
            return route_data.get('inbound') or route_data.get('outbound')
        
        if trip.trip_inbound is True:
            return route_data.get('outbound')
        
        trip_end = (trip.trip_end_location or "").lower().strip()
        
        for d in route_data.get('directions', []):
            if d['coords']:
                if not trip_end:
                    return d['coords']
                if d['last_stop'] and trip_end in d['last_stop']:
                    return d['coords']
        
        return route_data.get('outbound') or route_data.get('inbound')

    def clear_old_positions(self, now):
        """Clear positions for vehicles with completed trips."""
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
