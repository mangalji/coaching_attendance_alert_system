"""Shared, validated forms for institute operations."""

from django import forms
from django.contrib.auth import get_user_model

from .models import Batch, Institute, Profile, Session, Student, Subject


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
        return cleaned


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = [
            "name", "roll_number", "batches", "guardian_name", "guardian_phone",
            "guardian_whatsapp_opt_in", "active",
        ]


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
