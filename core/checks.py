import os

from django.conf import settings
from django.core.checks import Warning, register


@register("security")
def field_encryption_key_check(app_configs, **kwargs):
    """Warn (don't fail the deploy) when the WhatsApp token's encryption key is being
    derived from SECRET_KEY instead of its own dedicated key. That coupling means a
    SECRET_KEY leak or rotation also exposes/breaks the stored WhatsApp token."""
    if settings.DEBUG or os.getenv("FIELD_ENCRYPTION_KEY"):
        return []
    return [
        Warning(
            "FIELD_ENCRYPTION_KEY is not set, so the stored WhatsApp token's encryption "
            "key is derived from DJANGO_SECRET_KEY instead of a key of its own.",
            hint="Set FIELD_ENCRYPTION_KEY in .env (see .env.example for how to generate "
            "one) so the two secrets can be rotated independently and a SECRET_KEY leak "
            "doesn't also expose the WhatsApp token.",
            id="core.W001",
        )
    ]
