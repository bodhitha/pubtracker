#!/bin/sh
set -e
python manage.py migrate --noinput
# On OKD, GUNICORN_BIND is 127.0.0.1:8080 so only the login proxy in the same pod can reach the app.
exec gunicorn config.wsgi:application \
  --bind "${GUNICORN_BIND:-0.0.0.0:8080}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --access-logfile - \
  --forwarded-allow-ips "127.0.0.1"
