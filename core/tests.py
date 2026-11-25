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


import hashlib
import hmac
import json
import re

from django.core import mail
from django.test import override_settings
from django.core.exceptions import ValidationError

from .services import apply_delivery_receipt, dispatch_guardian_alerts, normalize_phone, retry_alert


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
class EndToEndTests(TestCase):
    """Walks through the whole product: invite -> OTP login -> schedule -> attendance -> alert."""

    def sign_in(self, client, email, password, portal):
        client.post(f"/login/{portal}/", {"email": email, "password": password})
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        return client.post("/verify/", {"code": code})

    def setUp(self):
        self.admin = User.objects.create_user("admin@x.com", "admin@x.com", "Adm1n-pass-77", is_superuser=True)
        self.institute = Institute.objects.create(name="Demo", whatsapp_enabled=True, alert_delay_minutes=1)
        self.batch = Batch.objects.create(name="JEE 2027")
        self.maths = Subject.objects.create(batch=self.batch, name="Maths")

    def invite_and_accept(self, admin_client, data, password):
        resp = admin_client.post("/accounts/invite/", data)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        url = re.search(r"http://testserver(/invite/\S+)", mail.outbox[-1].body).group(1)
        c = self.client_class()
        self.assertEqual(c.post(url, {"new_password1": password, "new_password2": password}).status_code, 302)
        return c

    def test_full_flow(self):
        admin_c = self.client_class()
        self.assertRedirects(self.sign_in(admin_c, "admin@x.com", "Adm1n-pass-77", "admin"), "/dashboard/", fetch_redirect_response=False)

        # invite a teacher and a student (invitation-based account creation)
        self.invite_and_accept(admin_c, {
            "name": "Tina", "email": "tina@x.com", "role": "teacher",
            "batches": [self.batch.pk], "subjects": [self.maths.pk],
        }, "Teach3r-pass-1")
        self.invite_and_accept(admin_c, {
            "name": "Sam", "email": "sam@x.com", "role": "student", "roll_number": "R1",
            "guardian_name": "Mr Sam", "guardian_phone": "98765 43210",
            "guardian_whatsapp_opt_in": "on", "batches": [self.batch.pk],
        }, "Stud3nt-pass-1")
        student = Student.objects.get(roll_number="R1")
        self.assertEqual(student.guardian_phone, "919876543210")

        # wrong portal is rejected
        c = self.client_class()
        c.post("/login/admin/", {"email": "tina@x.com", "password": "Teach3r-pass-1"})
        self.assertEqual(c.post("/verify/", {"code": "000000"}).status_code, 302)  # no session -> home
        self.assertNotIn("_auth_user_id", c.session)

        # admin schedules a weekly-repeating class
        start = timezone.now() - timedelta(hours=2)
        resp = admin_c.post("/manage/session/", {
            "batch": self.batch.pk, "subject": self.maths.pk,
            "starts_at": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "repeat_weekly_until": (timezone.localdate() + timedelta(days=15)).isoformat(),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Session.objects.count(), 3)
        session = Session.objects.order_by("starts_at").first()

        # teacher marks + finalizes attendance
        teacher_c = self.client_class()
        self.sign_in(teacher_c, "tina@x.com", "Teach3r-pass-1", "teacher")
        self.assertContains(teacher_c.get("/dashboard/"), "JEE 2027")
        teacher_c.post(f"/sessions/{session.pk}/attendance/", {f"status_{student.pk}": "absent", "finalize": "yes"})
        session.refresh_from_db()
        self.assertTrue(session.attendance_finalized)
        followup = FollowUp.objects.get()
        self.assertEqual(teacher_c.get("/followups/").status_code, 200)

        # scheduler: queue + send (WhatsApp mocked)
        self.institute.whatsapp_phone_number_id = "123"
        self.institute.set_whatsapp_access_token("tok")
        self.institute.save()
        session.finalized_at = timezone.now() - timedelta(minutes=5)
        session.save()
        with patch("core.services.requests.post") as post:
            post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            post.return_value.raise_for_status.return_value = None
            from .services import dispatch_pending_notifications
            self.assertEqual(dispatch_pending_notifications()["sent"], 1)
        alert = GuardianAlert.objects.get()
        self.assertEqual((alert.status, alert.provider_message_id), ("sent", "wamid.1"))
        self.assertEqual(post.call_args.kwargs["json"]["to"], "919876543210")

        # delivery receipt webhook
        with override_settings(WHATSAPP_APP_SECRET="s3cret"):
            body = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "delivered"}]}}]}]}).encode()
            sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
            self.assertEqual(self.client.post("/webhooks/whatsapp/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig).status_code, 200)
            self.assertEqual(self.client.post("/webhooks/whatsapp/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256="bad").status_code, 403)
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, "delivered")

        # follow-up update by teacher
        teacher_c.post(f"/followups/{followup.pk}/update/", {"status": "contacted", "reason": "Called, ill"})
        followup.refresh_from_db()
        self.assertEqual((followup.status, followup.reason), ("contacted", "Called, ill"))

        # student sees only own record with percentage
        student_c = self.client_class()
        self.sign_in(student_c, "sam@x.com", "Stud3nt-pass-1", "student")
        resp = student_c.get("/dashboard/")
        self.assertContains(resp, "0.0%")
        self.assertEqual(student_c.get("/reports/").status_code, 403)
        self.assertEqual(student_c.get("/followups/").status_code, 403)

        # reports + CSV
        resp = admin_c.get("/reports/?status=absent")
        self.assertContains(resp, "Attendance by student")
        csv_resp = admin_c.get("/reports/?format=csv")
        self.assertIn("Sam", csv_resp.content.decode())
        self.assertIn("Roll number", admin_c.get("/reports/?format=summary_csv").content.decode())

        # admin account management pages work
        tina = User.objects.get(email="tina@x.com")
        self.assertEqual(admin_c.get(f"/accounts/{tina.pk}/edit/").status_code, 200)
        self.assertEqual(admin_c.post(f"/accounts/{tina.pk}/edit/", {"first_name": "Tina R", "email": "tina@x.com", "is_active": "on"}).status_code, 302)

    def test_failed_alert_backoff_and_retry(self):
        s = Student.objects.create(name="A", roll_number="9", guardian_phone="919999999999", guardian_whatsapp_opt_in=True)
        s.batches.add(self.batch)
        sess = Session.objects.create(batch=self.batch, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1),
                                      attendance_finalized=True, finalized_at=timezone.now() - timedelta(minutes=5))
        att = save_attendance(sess, s, "absent", self.admin)
        alert = GuardianAlert.objects.create(attendance=att)
        self.institute.whatsapp_phone_number_id = "1"
        self.institute.set_whatsapp_access_token("t")
        self.institute.save()
        import requests as rq
        with patch("core.services.requests.post", side_effect=rq.ConnectionError("boom")):
            self.assertEqual(dispatch_guardian_alerts()["failed"], 1)
            alert.refresh_from_db()
            self.assertEqual(alert.status, "failed")
            self.assertIsNotNone(alert.next_attempt_at)
            self.assertEqual(dispatch_guardian_alerts()["failed"], 0)  # still backing off
        alert.attempts = 3
        alert.save()
        self.assertTrue(retry_alert(alert))
        alert.refresh_from_db()
        self.assertEqual((alert.status, alert.attempts), ("pending", 0))

    def test_forgot_password_flow(self):
        User.objects.create_user("p@x.com", "p@x.com", "Old-pass-1234")
        Profile.objects.create(user=User.objects.get(email="p@x.com"), role="teacher")
        self.client.post("/forgot-password/", {"email": "p@x.com"})
        url = re.search(r"http://testserver(/reset/\S+)", mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.post(url, {"new_password1": "New-pass-5678x", "new_password2": "New-pass-5678x"}).status_code, 302)
        self.assertTrue(User.objects.get(email="p@x.com").check_password("New-pass-5678x"))
        self.assertContains(self.client.get(url), "invalid or has expired")  # single use
        n = len(mail.outbox)
        self.client.post("/forgot-password/", {"email": "nobody@x.com"})
        self.assertEqual(len(mail.outbox), n)

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("+91 98765-43210"), "919876543210")
        self.assertEqual(normalize_phone("09876543210"), "919876543210")
        self.assertEqual(normalize_phone(""), "")
        with self.assertRaises(ValidationError):
            normalize_phone("12345")

    def test_subject_removal_rules(self):
        from .forms import BatchForm
        from .services import save_batch_with_subjects
        form = BatchForm({"name": "JEE 2027", "active": "on"}, instance=self.batch)
        self.assertTrue(form.is_valid())
        save_batch_with_subjects(form, ["Physics"])  # Maths has no classes -> removable
        self.assertEqual(list(self.batch.subjects.values_list("name", flat=True)), ["Physics"])
        phys = self.batch.subjects.get()
        Session.objects.create(batch=self.batch, subject=phys, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        form = BatchForm({"name": "JEE 2027", "active": "on"}, instance=self.batch)
        form.is_valid()
        with self.assertRaises(ValueError):
            save_batch_with_subjects(form, ["Chemistry"])
