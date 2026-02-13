# MyBusTimes V2
[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC_BY--NC--SA_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
![CodeRabbit Pull Request Reviews](https://img.shields.io/coderabbit/prs/github/NextStopLabs/MyBusTimes?utm_source=oss&utm_medium=github&utm_campaign=NextStopLabs%2FMyBusTimes&labelColor=171717&color=FF570A&link=https%3A%2F%2Fcoderabbit.ai&label=CodeRabbit+Reviews)

# Important notes
1. add your doimain to settings "CSRF_TRUSTED_ORIGINS"
2. Keep debug enabled to disable captcha
3. Only test on python 3.11.0

# How this works

This project is a Django application that tracks bus fleets, trips, and live status.
The core flow is:

1. Data is stored in Django models (fleet, trips, routes, operators).
2. A scheduled task hits a protected endpoint to simulate or update positions.
3. A management command updates the database with new positions and trip state.
4. The home page and APIs query the latest state to render stats and dashboards.

## Key runtime components

- **Django app**: Serves web pages and JSON endpoints.
- **Management commands**: Background jobs that update positions and schedules.
- **Cache layer**: Used for rate limiting and job locks.
- **Database**: Stores fleet, trip, and tracking data.

## Tracking update flow

### 1) Scheduled trigger

The endpoint `POST /api/trips/update_positions/` triggers the update.

- Uses a shared-secret header: `X-Cron-Secret`.
- Rate limited to 2 calls per minute per instance (cache key is per minute bucket).
- Protected by a cache-based lock to avoid overlapping runs.

### 2) Command execution

If the request passes auth and rate limits, the view runs:

```
python manage.py simulate_positions
```

That command updates the fleet and trip state (positions, trip progress, etc).

### 3) Response behavior

- `200` indicates the update ran.
- `202` means a previous run is still in progress.
- `429` means the per-minute limit was exceeded.

## Home page stats

The home page (`GET /`) renders a summary for the last update and system status:

- `last_updated`: Latest `fleet.updated_at` timestamp.
- `tracking_count`: Number of vehicles with an active `current_trip`.
- `total_vehicles`: Total number of fleet records.
- `active_trips`: Trips that started and have not finished recently.

`active_trips` uses two windows:

- Normal: trips with `trip_end_at >= now - 2 minutes`.
- Overnight/long trips: trips where `trip_end_at` is earlier than `trip_start_at`
    and the start time is within the last 8 hours.

## Health check

`GET /healthz/` returns `{ "status": "ok" }` for uptime monitoring.

## Key settings and environment

- `CRON_SECRET`: Shared secret for the update endpoint.
- `DEBUG`: Keep `True` in local dev to bypass captcha.
- `CSRF_TRUSTED_ORIGINS`: Required for domain-based deployments.
- Database and external services are configured via `.env` or settings.

## Where to look in code

- Endpoint logic: `tracking/views.py`
- Models: `fleet/models.py`, `tracking/models.py`, `routes/models.py`
- URLs: `tracking/urls.py` and `mybustimes/urls.py`
- Commands: `tracking/management/commands/`

# API usage

## Update simulated positions

POST `/api/trips/update_positions/`

- Requires `X-Cron-Secret` header matching `CRON_SECRET` in your settings/env
- Rate limited to 2 requests per minute per IP

```bash
curl -X POST \
    -H "X-Cron-Secret: your_secret_here" \
    http://localhost:8000/api/trips/update_positions/
```

### Possible responses
- `200` `{ "status": "ok", "updating": true }`
- `202` `{ "status": "already running" }`
- `429` `{ "status": "rate limit exceeded" }`

## Health check

GET `/healthz/`

```bash
curl http://localhost:8000/healthz/
```

## Home page

GET `/`

Shows last tracking update, number of buses tracking, and summary stats.

## .env setup

```
DEBUG=True
SECRET_KEY=
ALLOWED_HOSTS=

STRIPE_SECRET_KEY=sk_live_
STRIPE_PUBLISHABLE_KEY=pk_live_
STRIPE_WEBHOOK_SECRET=
STRIPE_BILLING_PORTAL_URL=https://billing.stripe.com/

STRIPE_PUBLISHABLE_KEY_TEST=pk_test_
STRIPE_SECRET_KEY_TEST=sk_test_
STRIPE_WEBHOOK_SECRET_TEST=

PRICE_ID_MONTHLY=price_
PRICE_ID_YEARLY=price_
PRICE_ID_CUSTOM=price_

PRICE_ID_MONTHLY_TEST=price_
PRICE_ID_YEARLY_TEST=price_
PRICE_ID_CUSTOM_TEST=price_

DISCORD_LIVERY_REQUESTS_CHANNEL_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_OPERATOR_TYPE_REQUESTS_CHANNEL_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_TYPE_REQUEST_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_FOR_SALE_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_WEB_ERROR_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_404_ERROR_WEBHOOK=https://discord.com/api/webhooks/
DISCORD_BOT_API_URL=http://localhost:8070

DISCORD_GUILD_ID=
DISCORD_BOT_API_TOKEN=

DISCORD_MIGRATION_ERROR_ID=
DISCORD_REPORTS_CHANNEL_ID=
DISCORD_LIVERY_ID=
DISCORD_GAME_ID=
DISCORD_OPERATOR_LOGS_ID=

DISCORD_GUILD_ID=
DISCORD_BOT_API_TOKEN=

DB_NAME=mybustimes
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=

SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=

CF_SITE_KEY=
CF_SECRET_KEY=

OIDC_RP_CLIENT_ID=
OIDC_RP_CLIENT_SECRET=
``` 

# Local Dev
## Inishel Setup

To run MBT local you can use sqlite

settings_local.py
```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
```

Then run the server
```bash
python manage.py runserver
```

Now it should be all setup and accessable from http://localhost:8000

# Setup

## DB Setup - Postgress

Update system
```bash
sudo apt update
sudo apt upgrade -y
```

Install postgres
```bash
sudo apt install postgresql postgresql-contrib nginx python3.11 python3.11-venv redis -y
```

Enable and start the service
```bash
sudo systemctl start postgresql
sudo systemctl enable postgresql
sudo systemctl enable redis
sudo systemctl start redis
```

Change to the postgres user
```bash
sudo -i -u postgres
```

Enter postgres
```bash
psql
```

Create the user and the db
```sql
CREATE USER mybustimesdb WITH PASSWORD 'your_secure_password';
CREATE DATABASE mybustimes OWNER mybustimesdb;
\c mybustimes
GRANT ALL ON SCHEMA public TO mybustimesdb;
ALTER SCHEMA public OWNER TO mybustimesdb;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO mybustimesdb;
ALTER USER mybustimesdb CREATEDB;
\q
```

Go back to the main user
```bash
exit
```

Test the connection
```bash
psql -h localhost -U username -d dbname
```

Exit if it worked
```
\q
```


## Web setup

Create the python venv
```bash
python3 -m venv .venv
```

Activate the venv
```bash
source .venv/bin/activate
```

Install python dependencies
```bash
pip install -r requirements.txt
```

Migrate main
```bash
python manage.py makemigrations
python manage.py migrate
```

Import base data
```bash
python manage.py loaddata data.json
```

Make your superuser
```bash
python manage.py createsuperuser
```

Create the service file
```bash
sudo nano /etc/systemd/system/mybustimes.service
```

Web service running on port 5681
```bash
[Unit]
Description=My Bus Times Django ASGI HTTP Workers (Gunicorn + Uvicorn)
After=network.target

[Service]
User=mybustimes
Group=mybustimes
WorkingDirectory=/srv/MyBusTimes
Environment="PATH=/srv/MyBusTimes/.venv/bin"
Environment="PYTHONUNBUFFERED=1"

ExecStart=/srv/MyBusTimes/.venv/bin/gunicorn \
    mybustimes.asgi:application \
    --workers 10 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:5681 \
    --log-level info \
    --access-logfile - \
    --error-logfile -

Restart=always
RestartSec=5
LimitNOFILE=4096
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Websocket running on port 5682
```bash
[Unit]
Description=My Bus Times Django ASGI WebSocket Worker
After=network.target

[Service]
User=mybustimes
Group=mybustimes
WorkingDirectory=/srv/MyBusTimes
Environment="PATH=/srv/MyBusTimes/.venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/srv/MyBusTimes/.venv/bin/uvicorn \
    mybustimes.asgi:application \
    --workers 1 \
    --host 127.0.0.1 \
    --port 5682 \
    --ws websockets \
    --log-level debug \
    --proxy-headers

Restart=always
RestartSec=5
LimitNOFILE=4096
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Reload Daemon
```bash
systemctl daemon-reload
```

Enable and start the web service
```bash
sudo systemctl start mybustimes
sudo systemctl start mybustimes-ws
sudo systemctl enable mybustimes
sudo systemctl enable mybustimes-ws
```

Check if its running
```bash
sudo systemctl status mybustimes
```

You show now be able to access it on http://localhost:5681
No styles will be loaded yet

## Setup Nginx
```bash
sudo nano /etc/nginx/sites-available/mybustimes
```

```bash
server {
    listen 4986;
    server_name mybustimes.cc www.mybustimes.cc;

    client_max_body_size 1G;

    # Static files
    location /static/ {
        alias /srv/MyBusTimes/staticfiles/;
        autoindex off;
    }

    # Media files
    location /media/ {
        alias /srv/MyBusTimes/media/;
        autoindex off;
    }

    error_page 502 /502.html;

    location = /502.html {
        root /usr/share/nginx/html;
        internal;
    }

    location /message/ws/ {
        proxy_pass http://127.0.0.1:5682;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;

        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    # Main proxy to frontend
    location / {
        proxy_pass http://127.0.0.1:5681;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Grant Nginx permitions to access mybustimes files
```bash
sudo chown -R your-user:www-data /path/to/MyBusTimes/staticfiles /path/to/MyBusTimes/media
sudo chmod -R 755 /path/to/MyBusTimes/staticfiles /path/to/MyBusTimes/media
sudo chmod +x /path/to/MyBusTimes
sudo chmod -R o+rx /path/to/MyBusTimes/staticfiles
sudo chmod -R o+rx /path/to/MyBusTimes/media
```

Reload Nginx
```bash
sudo ln -s /etc/nginx/sites-available/mybustimes /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Now it should be all setup and accessable from http://localhost
