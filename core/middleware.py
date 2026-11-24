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
