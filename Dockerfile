FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=mybustimes.settings

CMD ["gunicorn", "mybustimes.wsgi:application", \
     "--workers", "1", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60"]
