from django.db import models
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator


class Institute(models.Model):
    name = models.CharField(max_length=180)
    logo_url = models.URLField(blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    alert_delay_minutes = models.PositiveIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    whatsapp_enabled = models.BooleanField(default=False)
    whatsapp_access_token_encrypted = models.TextField(blank=True, editable=False)
    whatsapp_phone_number_id = models.CharField(max_length=180, blank=True)
    whatsapp_template_name = models.CharField(max_length=180, blank=True)
    whatsapp_template_language = models.CharField(max_length=20, blank=True)
    whatsapp_graph_version = models.CharField(max_length=20, blank=True)

    def set_whatsapp_access_token(self, value):
        from .crypto import encrypt_secret

        self.whatsapp_access_token_encrypted = encrypt_secret(value.strip())

    def get_whatsapp_access_token(self):
        from .crypto import decrypt_secret

        return decrypt_secret(self.whatsapp_access_token_encrypted)

    def __str__(self):
        return self.name


class Profile(models.Model):
    ROLES = [("admin", "Admin"), ("teacher", "Teacher"), ("student", "Student")]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=10, choices=ROLES, default="student")
    phone = models.CharField(max_length=25, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Batch(models.Model):
    name = models.CharField(max_length=140)
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="teaching_batches")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Subject(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="subjects")
    name = models.CharField(max_length=140)
    teachers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="teaching_subjects")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "name"], name="unique_batch_subject")]

    def __str__(self):
        return f"{self.batch} — {self.name}"


class Student(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_record",
    )
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=25, blank=True)
    guardian_whatsapp_opt_in = models.BooleanField(
        default=False,
        help_text="Consent has been obtained to send attendance alerts by WhatsApp.",
    )
    name = models.CharField(max_length=150)
    roll_number = models.CharField(max_length=40, unique=True)
    batches = models.ManyToManyField(Batch, related_name="students", blank=True)
    guardians = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="children", blank=True
    )
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Session(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="sessions")
    subject = models.ForeignKey(Subject, null=True, blank=True, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    attendance_finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "starts_at"], name="unique_batch_session"
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="session_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["starts_at"], name="session_starts_idx")]
        ordering = ["-starts_at"]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.subject_id and self.batch_id and self.subject.batch_id != self.batch_id:
            raise ValidationError({"subject": "Subject must belong to the selected batch."})

    def __str__(self):
        return f"{self.batch} {self.starts_at}"


class Attendance(models.Model):
    STATUS = [
        ("present", "Present"),
        ("absent", "Absent"),
        ("late", "Late"),
        ("excused", "Excused"),
    ]
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="attendance"
    )
    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, related_name="attendance"
    )
    status = models.CharField(max_length=10, choices=STATUS)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"], name="unique_student_attendance"
            )
        ]
        indexes = [
            models.Index(fields=["student", "status"], name="attendance_student_status_idx"),
            models.Index(fields=["status"], name="attendance_status_idx"),
        ]


class AttendanceAudit(models.Model):
    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="audit"
    )
    old_status = models.CharField(max_length=10, blank=True)
    new_status = models.CharField(max_length=10)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)


class FollowUp(models.Model):
    STATUS = [
        ("pending", "Pending"),
        ("contacted", "Contacted"),
        ("resolved", "Resolved"),
    ]
    attendance = models.OneToOneField(
        Attendance, on_delete=models.CASCADE, related_name="followup"
    )
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    reason = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "updated_at"], name="followup_status_updated_idx")]


class Notification(models.Model):
    STATUS = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]
    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="notifications"
    )
    guardian = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attendance", "guardian"],
                name="unique_absence_alert_per_guardian",
            )
        ]


class Invitation(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=64, unique=True)
    expires = models.DateTimeField()
    used = models.BooleanField(default=False)


class LoginChallenge(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=128)
    expires = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)


class AuthThrottle(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires = models.DateTimeField()


class GuardianAlert(models.Model):
    attendance = models.OneToOneField(Attendance, on_delete=models.CASCADE)
    STATUS = [
        ("pending", "Pending"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Needs review"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(max_length=12, choices=STATUS, default="pending")
    provider_message_id = models.CharField(max_length=180, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "attempts", "created_at"], name="alert_dispatch_idx")
        ]
