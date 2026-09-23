from django.conf import settings
from django.contrib.auth.middleware import PersistentRemoteUserMiddleware


class ProxyHeaderMiddleware(PersistentRemoteUserMiddleware):
    """Logs in the user named by the oauth-proxy sidecar.

    Only safe when the app is reachable solely through the proxy: on OKD, gunicorn binds
    to 127.0.0.1 inside the pod and the Service points at the proxy's port.
    """
    header = settings.AUTH_HEADER
