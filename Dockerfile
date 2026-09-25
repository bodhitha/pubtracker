# Red Hat's UBI Python image runs as a non-root user (1001) in group 0. Files are made group-writable
# so the image also works where the cluster picks an arbitrary UID in group 0 (OpenShift does this).
FROM registry.access.redhat.com/ubi9/python-312:latest

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/app-root/src
COPY --chown=1001:0 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=1001:0 . .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
 && chmod -R g=u /opt/app-root/src \
 && chmod +x entrypoint.sh

USER 1001
EXPOSE 8080
CMD ["./entrypoint.sh"]
