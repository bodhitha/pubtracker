#!/bin/sh
set -e
python manage.py migrate --noinput
# Only oauth2-proxy can reach the app on Kubernetes (NetworkPolicy), and it vouches for each user with
# AUTH_PROXY_SECRET, so the forwarded headers can be trusted from any address there (FORWARDED_ALLOW_IPS=*).
exec gunicorn config.wsgi:application \
  --bind "${GUNICORN_BIND:-0.0.0.0:8080}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --worker-tmp-dir /tmp \
  --access-logfile - \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
