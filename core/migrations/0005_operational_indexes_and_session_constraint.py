from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_guardian_contact_and_alert_tracking")]

    operations = [
        migrations.AddConstraint(
            model_name="session",
            constraint=models.CheckConstraint(
                condition=models.Q(("ends_at__gt", models.F("starts_at"))),
                name="session_end_after_start",
            ),
        ),
        migrations.AddIndex(
            model_name="session",
            index=models.Index(fields=["starts_at"], name="session_starts_idx"),
        ),
        migrations.AddIndex(
            model_name="attendance",
            index=models.Index(fields=["student", "status"], name="attendance_student_status_idx"),
        ),
        migrations.AddIndex(
            model_name="attendance",
            index=models.Index(fields=["status"], name="attendance_status_idx"),
        ),
        migrations.AddIndex(
            model_name="followup",
            index=models.Index(fields=["status", "updated_at"], name="followup_status_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="guardianalert",
            index=models.Index(fields=["status", "attempts", "created_at"], name="alert_dispatch_idx"),
        ),
    ]
