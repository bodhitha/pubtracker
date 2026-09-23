from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_set(context, key, value=""):
    """Current query string with one parameter changed (empty value removes it); resets paging."""
    params = context["request"].GET.copy()
    params.pop("page", None)
    if value in ("", None):
        params.pop(key, None)
    else:
        params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter
def role_list(contribution):
    labels = [r.label for r in contribution.roles.all() if r.code != "contributor"]
    return ", ".join(labels)
