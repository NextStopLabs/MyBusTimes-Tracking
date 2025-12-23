from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.management import call_command
from django.core.cache import cache
from django.conf import settings
from django.views.decorators.http import require_POST
import time
import secrets

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
