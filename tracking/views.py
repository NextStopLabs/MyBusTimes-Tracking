from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.core.management import call_command
from django.core.cache import cache
from django.conf import settings
from django.views.decorators.http import require_POST
from django.db.models import Max, Q, F
from django.utils import timezone
from datetime import timedelta
import time
import secrets

from fleet.models import fleet
from tracking.models import Trip

@require_POST
@csrf_exempt
def simulate_positions_view(request):
    # shared-secret auth
    expected = settings.CRON_SECRET
    provided = request.headers.get("X-Cron-Secret")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
         return JsonResponse({"status": "nope"}, status=200)

    now = int(time.time())
    window = now // 60  # current minute bucket
    key = f"simulate_positions_calls_{window}"

    calls = cache.get(key, 0)
    if calls >= 2:
        return JsonResponse(
            {"status": "rate limit exceeded"},
            status=429
        )

    # increment counter (expire after 60s)
    cache.set(key, calls + 1, timeout=60)

    # overlap protection
    if not cache.add("simulate_positions_lock", True, timeout=300):
        return JsonResponse({"status": "already running"}, status=202)

    try:
        call_command("simulate_positions")
        return JsonResponse({"status": "ok", "updating": True}, status=200)
    finally:
        cache.delete("simulate_positions_lock")


def home_view(request):
    now = timezone.now()
    two_mins_ago = now - timedelta(minutes=2)
    eight_hours_ago = now - timedelta(hours=8)

    last_updated = fleet.objects.exclude(updated_at__isnull=True).aggregate(
        last=Max("updated_at")
    )["last"]

    tracking_count = fleet.objects.filter(current_trip__isnull=False).count()
    total_vehicles = fleet.objects.count()

    active_trips = Trip.objects.filter(
        trip_missed=False,
        trip_start_at__lte=now,
    ).filter(
        Q(trip_end_at__gte=two_mins_ago) |
        Q(trip_end_at__lt=F("trip_start_at"), trip_start_at__gte=eight_hours_ago)
    ).count()

    context = {
        "last_updated": last_updated,
        "tracking_count": tracking_count,
        "total_vehicles": total_vehicles,
        "active_trips": active_trips,
    }

    return render(request, "tracking/home.html", context)


def healthz_view(request):
    return JsonResponse({"status": "ok"}, status=200)
