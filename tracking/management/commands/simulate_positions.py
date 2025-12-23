from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from tracking.models import Trip
from fleet.models import fleet

# Import your existing helper functions
from tracking.utils import (
    get_route_coordinates,
    get_progress,
    interpolate,
    calculate_heading,
)

class Command(BaseCommand):
    help = "Simulate vehicle positions for all active trips"

    def handle(self, *args, **kwargs):
        now = timezone.now()

        # ---------------------------------------------------------
        # 1. Get active trips (start <= now, and end >= now - 2 mins)
        # ---------------------------------------------------------
        active_trips = (    
            Trip.objects
            .filter(
                trip_start_at__lte=now, 
                trip_end_at__gte=now - timezone.timedelta(minutes=2),
                trip_missed=False
            )
            .select_related(
                "trip_vehicle", 
                "trip_vehicle__operator", 
                "trip_vehicle__livery", 
                "trip_vehicle__vehicleType", 
                "trip_route"
            )
        )

        if not active_trips.exists():
            self.stdout.write("No active trips found.")
            # Still clear old positions even if no active trips
            self.clear_old_positions(now)
            return
        
        # ---------------------------------------------------------
        # 2. Clear sim data for vehicles not on active trips
        # ---------------------------------------------------------
        self.clear_old_positions(now)

        # ---------------------------------------------------------
        # 3. Pre-fetch route coordinates
        # ---------------------------------------------------------
        route_ids = set(trip.trip_route_id for trip in active_trips if trip.trip_route_id)
        from routes.models import routeStop
        route_stops = routeStop.objects.filter(route_id__in=route_ids).order_by("id")
        
        # Map route_id -> list of routeStop objects
        route_stops_map = {}
        for rs in route_stops:
            if rs.route_id not in route_stops_map:
                route_stops_map[rs.route_id] = []
            route_stops_map[rs.route_id].append(rs)

        # ---------------------------------------------------------
        # 4. Update each active trip
        # ---------------------------------------------------------
        vehicles_to_update = []
        
        for trip in active_trips:
            vehicle = trip.trip_vehicle
            if not vehicle:
                continue

            # Load route shape from pre-fetched data
            stops_qs = route_stops_map.get(trip.trip_route_id, [])
            coords = self.get_coords_from_prefetched(stops_qs, trip)
            
            if not coords:
                continue

            # Compute progress (0..1)
            progress = get_progress(trip, now=now)

            if progress >= 1:
                lat, lng = coords[-1]
                heading = vehicle.sim_heading or 0
            else:
                # Interpolate coordinate
                lat, lng, seg_index = interpolate(coords, progress)

                # If we're at the last point, re-use previous point
                if seg_index >= len(coords) - 1:
                    lat2, lng2 = coords[seg_index - 1]
                else:
                    lat2, lng2 = coords[seg_index + 1]

                heading = calculate_heading(lat, lng, lat2, lng2)

            # Update vehicle object in memory
            vehicle.sim_lat = lat
            vehicle.sim_lon = lng
            vehicle.sim_heading = heading
            vehicle.current_trip = trip
            vehicle.updated_at = now
            vehicles_to_update.append(vehicle)

        # ---------------------------------------------------------
        # 5. Bulk update vehicles
        # ---------------------------------------------------------
        if vehicles_to_update:
            fleet.objects.bulk_update(
                vehicles_to_update,
                ["sim_lat", "sim_lon", "sim_heading", "current_trip", "updated_at"]
            )
            self.stdout.write(f"Bulk updated {len(vehicles_to_update)} vehicles.")

    def clear_old_positions(self, now):
        updated_count = fleet.objects.filter(
            current_trip__trip_end_at__lt=now - timezone.timedelta(minutes=15)
        ).update(
            sim_lat=None,
            sim_lon=None,
            sim_heading=None,
            current_trip=None,
            updated_at=None
        )
        if updated_count > 0:
            self.stdout.write(f"Cleared {updated_count} old trip positions.")

    def get_coords_from_prefetched(self, stops_qs, trip):
        """
        Logic adapted from tracking.utils.get_route_coordinates 
        but using pre-fetched stops_qs.
        """
        from tracking.utils import extract_coords_from_routeStop, extract_coords_and_last_stop
        
        if not stops_qs:
            return []

        if trip.trip_inbound is False:
            if len(stops_qs) >= 2:
                return extract_coords_from_routeStop(stops_qs[1])
            return extract_coords_from_routeStop(stops_qs[0])

        if trip.trip_inbound is True:
            return extract_coords_from_routeStop(stops_qs[0])

        # Fallback: Auto-detect
        direction_candidates = []
        for rs in stops_qs:
            coords, last_stop_name = extract_coords_and_last_stop(rs)
            if coords:
                direction_candidates.append({
                    "coords": coords,
                    "last_stop": last_stop_name
                })

        if not direction_candidates:
            return []

        trip_end_location = (trip.trip_end_location or "").lower().strip()
        for d in direction_candidates:
            ls = (d["last_stop"] or "").lower().strip()
            if trip_end_location and ls and trip_end_location in ls:
                return d["coords"]

        return direction_candidates[0]["coords"]
