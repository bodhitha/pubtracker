import base64
import binascii
import hmac

from django.conf import settings
from django.contrib.auth.middleware import RemoteUserMiddleware


def proxy_user(authorization):
    """The user oauth2-proxy vouches for, or None.

    oauth2-proxy (with pass-basic-auth and basic-auth-password) sends "Basic base64(email:password)".
    Only a request whose password matches AUTH_PROXY_SECRET came through the proxy.
    """
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not settings.AUTH_PROXY_SECRET:
        return None
    try:
        username, _, password = base64.b64decode(encoded.strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if username and hmac.compare_digest(password.encode(), settings.AUTH_PROXY_SECRET.encode()):
        return username.lower()  # emails; sign-in services don't always agree on capitalisation
    return None


class ProxyAuthMiddleware(RemoteUserMiddleware):
    """Logs in the user oauth2-proxy signed in, checked on every request.

    Not the persistent variant: a session cookie alone is not enough, so a request that didn't come
    through the proxy is anonymous even if it carries a session from an earlier one.
    """
    header = "PUBTRACKER_PROXY_USER"  # not an HTTP_* key, so a client can't set it with a header

    def __call__(self, request):
        request.META.pop(self.header, None)
        if username := proxy_user(request.META.get("HTTP_AUTHORIZATION", "")):
            request.META[self.header] = username
        return super().__call__(request)
