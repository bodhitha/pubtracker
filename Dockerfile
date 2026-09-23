# Red Hat's UBI Python image already runs as a non-root user in group 0, which is what
# OKD expects: it starts containers under a random UID that belongs to group 0.
FROM registry.access.redhat.com/ubi9/python-312:latest

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
