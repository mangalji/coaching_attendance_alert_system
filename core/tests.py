import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import (
    Institute,
    Profile,
    Batch,
    Subject,
    Student,
    Session,
    Attendance,
    AttendanceAudit,
    FollowUp,
    GuardianAlert,
)
from .forms import SessionForm
from .services import claim_alert, queue_due_notifications, save_attendance
from config.database import build_database_config

User = get_user_model()


class WorkflowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_test", password="strong-pass-123"
        )
        Profile.objects.create(user=self.admin, role="admin")
        self.parent = User.objects.create_user(
            username="parent_test", password="strong-pass-123"
        )
        Profile.objects.create(user=self.parent, role="parent")
        self.batch = Batch.objects.create(name="Test batch")
        self.batch.teachers.add(self.admin)
        self.student = Student.objects.create(name="Student", roll_number="001")
        self.student.batches.add(self.batch)
        self.student.guardians.add(self.parent)
        self.session = Session.objects.create(
            batch=self.batch,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )

    def test_idempotent_attendance_and_audit(self):
        save_attendance(self.session, self.student, "absent", self.admin)
        save_attendance(self.session, self.student, "absent", self.admin)
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(AttendanceAudit.objects.count(), 1)
        self.assertEqual(FollowUp.objects.count(), 1)
        save_attendance(self.session, self.student, "present", self.admin)
        self.assertEqual(FollowUp.objects.first().status, "resolved")
        self.assertEqual(AttendanceAudit.objects.count(), 2)

    def test_parent_cannot_mark_attendance(self):
        self.client.force_login(self.parent)
        self.assertEqual(
            self.client.get(f"/sessions/{self.session.pk}/attendance/").status_code, 302
        )

    def test_no_alerts_when_disabled(self):
        Institute.objects.create(name="Test", whatsapp_enabled=False)
        save_attendance(self.session, self.student, "absent", self.admin)
        self.assertEqual(queue_due_notifications(), 0)

    def test_whatsapp_token_is_encrypted_at_rest(self):
        institute = Institute.objects.create(name="Test")
        institute.set_whatsapp_access_token("meta-private-token")
        institute.save()
        institute.refresh_from_db()
        self.assertNotEqual(institute.whatsapp_access_token_encrypted, "meta-private-token")
        self.assertEqual(institute.get_whatsapp_access_token(), "meta-private-token")

    def test_alert_queue_requires_guardian_consent(self):
        Institute.objects.create(name="Test", whatsapp_enabled=True, alert_delay_minutes=1)
        self.session.attendance_finalized = True
        self.session.finalized_at = timezone.now() - timedelta(minutes=2)
        self.session.save()
        self.student.guardian_phone = "919999999999"
        self.student.save()
        save_attendance(self.session, self.student, "absent", self.admin)
        self.assertEqual(queue_due_notifications(), 0)
        self.student.guardian_whatsapp_opt_in = True
        self.student.save()
        self.assertEqual(queue_due_notifications(), 1)
        self.assertEqual(GuardianAlert.objects.count(), 1)

    def test_profile_admin_can_access_management_portal(self):
        admin = User.objects.create_user("office", "office@example.com", "strong-pass-123")
        Profile.objects.create(user=admin, role="admin")
        self.client.force_login(admin)
        session = self.client.session
        session["otp_verified"] = True
        session.save()
        self.assertEqual(self.client.get("/students/").status_code, 200)
        self.assertEqual(self.client.get("/manage/batch/").status_code, 200)
        self.assertEqual(self.client.get("/reports/?status=present").status_code, 200)

    def test_admin_can_delete_an_empty_batch(self):
        empty_batch = Batch.objects.create(name="Empty batch")
        self.client.force_login(self.admin)
        session = self.client.session
        session["otp_verified"] = True
        session.save()
        response = self.client.post(f"/manage/batch/{empty_batch.pk}/delete/")
        self.assertRedirects(response, "/batches/")
        self.assertFalse(Batch.objects.filter(pk=empty_batch.pk).exists())

    def test_session_form_rejects_invalid_subject_and_time_range(self):
        other_batch = Batch.objects.create(name="Other batch")
        subject = Subject.objects.create(batch=other_batch, name="Physics")
        form = SessionForm(
            data={
                "batch": self.batch.pk,
                "subject": subject.pk,
                "starts_at": "2026-01-01T12:00",
                "ends_at": "2026-01-01T11:00",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ends_at", form.errors)
        self.assertIn("subject", form.errors)

    def test_alert_can_only_be_claimed_once(self):
        attendance = save_attendance(self.session, self.student, "absent", self.admin)
        alert = GuardianAlert.objects.create(attendance=attendance)
        self.assertEqual(claim_alert(alert.pk), 1)
        self.assertEqual(claim_alert(alert.pk), 0)
        alert.refresh_from_db()
        self.assertEqual(alert.status, "sending")
        self.assertEqual(alert.attempts, 1)

    def test_parent_only_own_child(self):
        other = Student.objects.create(name="Other", roll_number="002")
        other.batches.add(self.batch)
        self.parent.profile.role = "student"
        self.parent.profile.save()
        self.student.user = self.parent
        self.student.save()
        self.client.force_login(self.parent)
        session = self.client.session
        session["otp_verified"] = True
        session.save()
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Student")
        self.assertNotContains(response, "Other")


class DatabaseConfigurationTests(SimpleTestCase):
    def test_explicit_database_engines(self):
        base_dir = Path("C:/temporary/coaching-suite")
        common = {
            "DATABASE_URL": "",
            "DATABASE_NAME": "coaching_data",
            "DATABASE_USER": "portal_user",
            "DATABASE_PASSWORD": "secret",
            "DATABASE_HOST": "db.internal",
        }
        with patch.dict(os.environ, {**common, "DATABASE_ENGINE": "sqlite"}):
            sqlite = build_database_config(base_dir)
        with patch.dict(os.environ, {**common, "DATABASE_ENGINE": "postgresql"}):
            postgres = build_database_config(base_dir)
        with patch.dict(os.environ, {**common, "DATABASE_ENGINE": "mysql"}):
            mysql = build_database_config(base_dir)

        self.assertEqual(sqlite["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(postgres["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(mysql["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(mysql["OPTIONS"]["charset"], "utf8mb4")
