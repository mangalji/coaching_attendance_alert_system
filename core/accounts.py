import hashlib
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import (
    AuthThrottle,
    Batch,
    Institute,
    Invitation,
    LoginChallenge,
    Profile,
    Student,
    Subject,
)
from .forms import UserAccountForm

User = get_user_model()
ROLES = ("admin", "teacher", "student")


def role_of(user):
    if user.is_superuser:
        return "admin"
    r = getattr(getattr(user, "profile", None), "role", None)
    return r if r in ("admin", "teacher", "student") else None


def is_admin(user):
    return user.is_superuser or role_of(user) == "admin"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@transaction.atomic
def limited(key, maximum=8, minutes=15):
    row, _ = AuthThrottle.objects.select_for_update().get_or_create(
        key=digest(key),
        defaults={"expires": timezone.now() + timedelta(minutes=minutes)},
    )
    if row.expires <= timezone.now():
        row.count = 0
        row.expires = timezone.now() + timedelta(minutes=minutes)
    if row.count >= maximum:
        return True
    row.count += 1
    row.save()
    return False


def page(request, title, form=None, error="", **extra):
    return render(
        request,
        "core/account.html",
        {
            "title": title,
            "form": form,
            "error": error,
            "institute": Institute.objects.first(),
            **extra,
        },
    )


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class OTPForm(forms.Form):
    code = forms.RegexField(regex=r"^\d{6}$", max_length=6)


@never_cache
def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return page(request, "Choose your portal", landing=True)


@never_cache
@require_http_methods(["GET", "POST"])
def sign_in(request, portal):
    if portal not in ROLES:
        return HttpResponseForbidden()
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].lower()
        if limited("ip:" + request.META.get("REMOTE_ADDR", ""), 30) or limited(
            "login:" + email
        ):
            return page(request, "Sign in", form, "Too many attempts. Try later.")
        users = list(User.objects.filter(email__iexact=email)[:2])
        user = (
            authenticate(
                request,
                username=users[0].username,
                password=form.cleaned_data["password"],
            )
            if len(users) == 1
            else None
        )
        if user and role_of(user) == portal:
            code = f"{secrets.randbelow(1000000):06d}"
            hashed = make_password(code)
            LoginChallenge.objects.update_or_create(
                user=user,
                defaults={
                    "digest": hashed,
                    "expires": timezone.now() + timedelta(minutes=5),
                    "attempts": 0,
                },
            )
            try:
                send_mail(
                    "Your sign-in code",
                    f"Your code is {code}. Expires in 5 minutes. Do not share it.",
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                )
                print(
                    f"Your sign-in code: Your code is {code}. Expires in 5 minutes. Do not share it, {settings.DEFAULT_FROM_EMAIL},{user.email} "
                )
            except Exception:
                return page(
                    request,
                    "Sign in",
                    form,
                    "Email delivery failed. Contact administrator.",
                )
            request.session.cycle_key()
            request.session["otp_user"] = user.pk
            request.session["otp_role"] = portal
            request.session["otp_challenge"] = digest(hashed)
            return redirect("otp")
        error = "Invalid credentials or portal."
    return page(request, portal.title() + " login", form, error)


@never_cache
@require_http_methods(["GET", "POST"])
def otp(request):
    uid = request.session.get("otp_user")
    form = OTPForm(request.POST or None)
    if not uid:
        return redirect("home")
    error = ""
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            challenge = (
                LoginChallenge.objects.select_for_update().filter(user_id=uid).first()
            )
            if (
                challenge
                and secrets.compare_digest(
                    digest(challenge.digest), request.session.get("otp_challenge", "")
                )
                and challenge.expires > timezone.now()
                and challenge.attempts < 5
            ):
                challenge.attempts += 1
                challenge.save(update_fields=["attempts"])
                user = challenge.user
                if (
                    check_password(form.cleaned_data["code"], challenge.digest)
                    and user.is_active
                    and role_of(user) == request.session.get("otp_role")
                ):
                    challenge.delete()
                    request.session.flush()
                    login(
                        request,
                        user,
                        backend="django.contrib.auth.backends.ModelBackend",
                    )
                    request.session["otp_verified"] = True
                    return redirect("dashboard")
        error = "Invalid or expired code. Start sign-in again if needed."
    return page(request, "Verify email OTP", form, error)


class InviteForm(forms.Form):
    name = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=[("teacher", "Teacher"), ("student", "Student"), ("admin", "Administrator")])
    roll_number = forms.CharField(max_length=40, required=False)
    guardian_name = forms.CharField(max_length=150, required=False)
    guardian_phone = forms.CharField(max_length=25, required=False)
    guardian_whatsapp_opt_in = forms.BooleanField(required=False)
    batches = forms.ModelMultipleChoiceField(
        queryset=Batch.objects.filter(active=True), required=False, widget=forms.CheckboxSelectMultiple
    )

    subjects = forms.ModelMultipleChoiceField(queryset=Subject.objects.filter(batch__active=True).select_related("batch"), required=False, widget=forms.CheckboxSelectMultiple)

    def clean(self):
        d = super().clean()
        email = d.get("email", "").lower()
        if User.objects.filter(email__iexact=email).exists():
            self.add_error("email", "Email already registered.")
        if d.get("role") in ("teacher", "student") and not d.get("batches"):
            self.add_error("batches", "Select at least one batch.")
        if d.get("role") == "student" and (
            not d.get("roll_number")
            or Student.objects.filter(roll_number=d["roll_number"]).exists()
        ):
            self.add_error("roll_number", "Unique roll number required.")
        if d.get("role") == "teacher" and d.get("batches") is not None and d.get("subjects") is not None:
            if set(d["subjects"].values_list("batch_id", flat=True)) != set(d["batches"].values_list("pk", flat=True)):
                self.add_error("subjects", "Select at least one subject in each selected batch, and no subjects outside those batches.")
        return d


@login_required
def invite(request):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    selected = request.GET.get("role", "student")

    if selected not in ("student", "teacher", "admin"):
        selected = "student"

    form = InviteForm(request.POST or None, initial={"role": selected})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        token = secrets.token_urlsafe(32)
        with transaction.atomic():
            u = User.objects.create_user(
                username=d["email"].lower(),
                email=d["email"].lower(),
                first_name=d["name"][:150],
                is_active=False,
            )
            Profile.objects.create(user=u, role=d["role"])
            if d["role"] == "student":
                s = Student.objects.create(
                    user=u,
                    name=d["name"],
                    roll_number=d["roll_number"],
                    guardian_name=d["guardian_name"],
                    guardian_phone=d["guardian_phone"],
                    guardian_whatsapp_opt_in=d["guardian_whatsapp_opt_in"],
                )
                s.batches.set(d["batches"])
            elif d["role"] == "teacher":
                for batch in d["batches"]:
                    batch.teachers.add(u)
                for subject in d["subjects"]:
                    subject.teachers.add(u)
            Invitation.objects.create(
                user=u,
                digest=digest(token),
                expires=timezone.now() + timedelta(hours=48),
            )
        try:
            deliver_invite(u, token)
        except Exception:
            return page(
                request, "Invitation saved", error="Email failed. Use resend in Users."
            )
        return redirect("accounts")
    return render(
        request,
        "core/manage_form.html",
        {
            "form": form,
            "title": "Add User — invite teacher or student",
            "invite_form": True,
            "role": "admin",
            "institute": Institute.objects.first(),
        },
    )


def deliver_invite(user, token):
    url = settings.PUBLIC_BASE_URL.rstrip("/") + reverse("accept_invite", args=[token])
    send_mail(
        "Your coaching portal invitation",
        f"Create your password within 48 hours: {url}",
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
    )


@login_required
def accounts(request):
    if not is_admin(request.user):
        return HttpResponseForbidden()

    selected = request.GET.get("role", "all")

    if selected not in ("all", "student", "teacher", "admin"):
        selected = "all"

    qs = (
        User.objects.filter(profile__role__in=["admin", "teacher", "student"])
        .select_related("profile")
        .order_by("first_name", "email")
    )
    if selected != "all":
        qs = qs.filter(profile__role=selected)

    return render(
        request,
        "core/accounts.html",
        {
            "accounts": qs,
            "selected": selected,
            "role": "admin",
            "institute": Institute.objects.first(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_account(request, pk):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    user = get_object_or_404(User, pk=pk, profile__role__in=ROLES)
    form = UserAccountForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Account updated.")
        return redirect("accounts")
    return render(
        request,
        "core/manage_form.html",
        {
            "form": form,
            "title": "Edit account",
            "role": role_of(request.user),
            "institute": Institute.objects.first(),
        },
    )


@login_required
@require_http_methods(["POST"])
def delete_account(request, pk):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    user = get_object_or_404(User, pk=pk, profile__role__in=ROLES)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot delete your own account.")
    elif role_of(user) == "admin" and Profile.objects.filter(role="admin").count() <= 1:
        messages.error(request, "At least one administrator account must remain.")
    else:
        user.delete()
        messages.success(request, "Account deleted.")
    return redirect("accounts")


@login_required
@require_http_methods(["POST"])
def resend(request, pk):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    u = get_object_or_404(
        User, pk=pk, is_active=False, profile__role__in=["admin", "teacher", "student"]
    )
    token = secrets.token_urlsafe(32)
    Invitation.objects.update_or_create(
        user=u,
        defaults={
            "digest": digest(token),
            "expires": timezone.now() + timedelta(hours=48),
            "used": False,
        },
    )
    try:
        deliver_invite(u, token)
    except Exception:
        return page(request, "Email failed", error="Check SMTP settings and retry.")
    return redirect("accounts")


@never_cache
@require_http_methods(["GET", "POST"])
def accept(request, token):
    with transaction.atomic():
        item = (
            Invitation.objects.select_for_update()
            .filter(
                digest=digest(token),
                used=False,
                expires__gt=timezone.now(),
                user__is_active=False,
            )
            .first()
        )
        if not item:
            return page(
                request,
                "Invitation unavailable",
                error="Ask your administrator for a new invitation.",
            )
        form = SetPasswordForm(item.user, request.POST or None)
        if request.method == "POST" and form.is_valid():
            u = form.save(commit=False)
            u.is_active = True
            u.save()
            item.used = True
            item.save(update_fields=["used"])
            return redirect("home")
        return page(request, "Create your password", form)

