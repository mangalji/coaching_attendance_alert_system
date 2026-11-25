"""Shared, validated forms for institute operations."""

from django import forms
from django.contrib.auth import get_user_model

from datetime import timedelta

from .models import Batch, Institute, Profile, Session, Student, Subject
from .services import normalize_phone


class InstituteSettingsForm(forms.ModelForm):
    whatsapp_access_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to retain the saved token. The value is encrypted before storage.",
    )

    class Meta:
        model = Institute
        fields = [
            "name", "logo_url", "phone", "email", "address",
            "alert_delay_minutes", "whatsapp_enabled", "whatsapp_phone_number_id",
            "whatsapp_template_name", "whatsapp_template_language", "whatsapp_graph_version",
        ]

    def save(self, commit=True):
        institute = super().save(commit=False)
        token = self.cleaned_data.get("whatsapp_access_token")
        if token:
            institute.set_whatsapp_access_token(token)
        if commit:
            institute.save()
        return institute


class BatchForm(forms.ModelForm):
    class Meta:
        model = Batch
        fields = ["name", "teachers", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teachers"].queryset = get_user_model().objects.filter(
            profile__role="teacher", is_active=True
        ).order_by("first_name", "email")


class SessionForm(forms.ModelForm):
    repeat_weekly_until = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Optional: also schedule this class every week on the same day until this date (max 1 year).",
    )

    class Meta:
        model = Session
        fields = ["batch", "subject", "starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean(self):
        cleaned = super().clean()
        batch, subject = cleaned.get("batch"), cleaned.get("subject")
        starts_at, ends_at = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "End must be after start.")
        if batch and subject and subject.batch_id != batch.pk:
            self.add_error("subject", "Subject must belong to the selected batch.")
        until = cleaned.get("repeat_weekly_until")
        if until:
            if self.instance.pk:
                self.add_error("repeat_weekly_until", "Repeats can only be set when creating a class.")
            elif starts_at and until < starts_at.date():
                self.add_error("repeat_weekly_until", "Repeat date must be after the first class.")
            elif starts_at and (until - starts_at.date()).days > 366:
                self.add_error("repeat_weekly_until", "Repeat for at most one year.")
        if batch and starts_at and not self.errors:
            clash = Session.objects.filter(batch=batch, starts_at=starts_at).exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("starts_at", "This batch already has a class at that time.")
        return cleaned

    def save_with_repeats(self):
        """Save the class and any weekly repeats; returns the number of sessions created."""
        first = self.save()
        until = self.cleaned_data.get("repeat_weekly_until")
        created = 1
        if until:
            step = 1
            while True:
                starts = first.starts_at + timedelta(weeks=step)
                if starts.date() > until:
                    break
                _, made = Session.objects.get_or_create(
                    batch=first.batch,
                    starts_at=starts,
                    defaults={
                        "subject": first.subject,
                        "ends_at": first.ends_at + timedelta(weeks=step),
                    },
                )
                created += int(made)
                step += 1
        return created


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = [
            "name", "roll_number", "batches", "guardian_name", "guardian_phone",
            "guardian_whatsapp_opt_in", "active",
        ]

    def clean_roll_number(self):
        return self.cleaned_data["roll_number"].strip()

    def clean_guardian_phone(self):
        return normalize_phone(self.cleaned_data.get("guardian_phone"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("guardian_whatsapp_opt_in") and not cleaned.get("guardian_phone"):
            self.add_error("guardian_phone", "A phone number is required when WhatsApp alerts are enabled.")
        return cleaned


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["batch", "name", "teachers"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teachers"].queryset = get_user_model().objects.filter(
            profile__role="teacher", is_active=True
        ).order_by("first_name", "email")


class UserAccountForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "email", "is_active"]

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        users = get_user_model().objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if users.exists():
            raise forms.ValidationError("An account already uses this email address.")
        return email

    def save(self, commit=True):
        user = super().save(commit=commit)
        record = getattr(user, "student_record", None)
        if commit and record and user.first_name and record.name != user.first_name:
            record.name = user.first_name
            record.save(update_fields=["name"])
        return user
