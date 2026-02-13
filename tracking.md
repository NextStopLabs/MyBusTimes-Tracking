# Tracking views walkthrough

This document explains each part of the tracking views file.

## File overview

The tracking views module exposes three endpoints:

- `simulate_positions_view`: protected POST endpoint that triggers a management
  command to simulate/refresh positions.
- `home_view`: renders the tracking home page with summary stats.
- `healthz_view`: returns a simple JSON status for health checks.

## Imports

- `JsonResponse` and `render`: return JSON or HTML responses.
- `csrf_exempt` and `require_POST`: enforce POST-only and bypass CSRF for the cron
  endpoint.
- `call_command`: runs the management command that updates positions.
- `cache`: used for rate limiting and locking.
- `settings`: reads `CRON_SECRET` from Django settings.
- `Max`, `Q`, `F`: build query expressions for stats.
- `timezone` and `timedelta`: compute time windows.
- `time` and `secrets`: access Unix time and constant-time secret comparison.
- `fleet` and `Trip`: database models used by the views.

## simulate_positions_view

Purpose: allow a cron or scheduler to trigger a one-off position update safely.

Steps:

1. Auth with a shared secret header.
   - Reads `CRON_SECRET` from settings.
   - Compares it to `X-Cron-Secret` using `secrets.compare_digest`.
   - Returns `{ "status": "nope" }` if missing or invalid.

2. Rate limit per minute.
   - Uses `time.time()` to derive a minute bucket.
   - Counts requests via the cache key `simulate_positions_calls_<minute>`.
   - Allows up to 2 requests per minute.
   - Returns `429` if the limit is exceeded.

3. Overlap protection.
   - Uses a cache lock key `simulate_positions_lock` with a 5 minute TTL.
   - Returns `202` if another run is already in progress.

4. Run the update command.
   - Executes `call_command("simulate_positions")`.
   - Returns `{ "status": "ok", "updating": true }` on success.

5. Always release the lock.
   - The lock is deleted in `finally` to avoid stale locks.

## home_view

Purpose: render the tracking home page with summary stats.

Steps:

1. Compute time windows.
   - `two_mins_ago` is used to define recently-ended trips.
   - `eight_hours_ago` handles overnight/long trips.

2. Determine last updated timestamp.
   - Uses `Max("updated_at")` across all fleet records.

3. Count currently tracking vehicles.
   - `current_trip__isnull=False` indicates an active trip is assigned.

4. Count total fleet records.
   - Simple `fleet.objects.count()`.

5. Count active trips.
   - Trips that started and either:
     - Ended within the last 2 minutes, or
     - Have an end time before the start time (overnight) and started within
       the last 8 hours.

6. Render the template.
   - Uses `tracking/home.html` with the computed context.

## healthz_view

Purpose: simple uptime endpoint for load balancers and monitors.

Behavior:

- Returns `{"status": "ok"}` with HTTP 200.
