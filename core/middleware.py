from django.contrib.auth import logout
from django.shortcuts import redirect
from .accounts import role_of


class VerifiedSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and (
            not request.session.get("otp_verified") or not role_of(request.user)
        ):
            logout(request)
        if request.path.startswith("/admin/") and not (
            request.user.is_authenticated and request.user.is_superuser
        ):
            return redirect("home")
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Adds CSP and Permissions-Policy. Inline scripts are used by a few pages, hence 'unsafe-inline'."""

    CSP = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data:; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'; object-src 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        user = getattr(request, "user", None)  # absent when an earlier middleware rejected the request
        if user is not None and user.is_authenticated:
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response
