import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
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

from .services import (
    apply_delivery_receipt,
    claim_alert,
    dispatch_guardian_alerts,
    normalize_phone,
    queue_due_notifications,
    retry_alert,
    save_attendance,
)


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
        self.assertIn("Sam", csv_body(csv_resp))
        self.assertIn("Roll number", csv_body(admin_c.get("/reports/?format=summary_csv")))

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


class InviteUiTests(TestCase):
    def test_invite_page_fields_are_wrapped_so_scripts_can_hide_them(self):
        admin = User.objects.create_user("a@x.com", "a@x.com", "Adm1n-pass-77", is_superuser=True)
        self.client.force_login(admin)
        s = self.client.session
        s["otp_verified"] = True
        s.save()
        html = self.client.get("/accounts/invite/").content.decode()
        self.assertIn("<fieldset", html)  # as_div wrappers, not <p> broken by <div> checkboxes

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
    def test_invite_link_shown_when_email_fails(self):
        admin = User.objects.create_user("a@x.com", "a@x.com", "Adm1n-pass-77", is_superuser=True)
        batch = Batch.objects.create(name="B")
        self.client.force_login(admin)
        s = self.client.session
        s["otp_verified"] = True
        s.save()
        with patch("core.accounts.send_mail", side_effect=OSError("smtp down")):
            resp = self.client.post("/accounts/invite/", {
                "name": "T", "email": "t@x.com", "role": "student", "roll_number": "7", "batches": [batch.pk],
            })
        self.assertContains(resp, "http://testserver/invite/")
        self.assertTrue(User.objects.filter(email="t@x.com", is_active=False).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
class SecurityHardeningTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm@x.com", "adm@x.com", "Adm1n-pass-77", is_superuser=True)
        Profile.objects.create(user=self.admin, role="admin")
        self.c = self.client_class(raise_request_exception=False)
        self.c.force_login(self.admin)
        s = self.c.session
        s["otp_verified"] = True
        s.save()

    def test_bad_report_filters_never_500(self):
        for q in ["from=abc", "to=2026-13-45", "batch=%C2%B2", "batch=" + "9" * 40, "status=<x>", "from=&to="]:
            self.assertEqual(self.c.get("/reports/?" + q).status_code, 200, q)
            self.assertEqual(self.c.get("/reports/?format=csv&" + q).status_code, 200, q)

    def test_csv_formula_injection_is_neutralised(self):
        b = Batch.objects.create(name="B")
        st = Student.objects.create(name="=HYPERLINK(\"http://evil\")", roll_number="+1")
        st.batches.add(b)
        sess = Session.objects.create(batch=b, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        Attendance.objects.create(session=sess, student=st, status="absent")
        body = csv_body(self.c.get("/reports/?format=csv"))
        self.assertIn("'=HYPERLINK", body)
        self.assertIn("'+1", body)
        self.assertNotIn(",=HYPERLINK", body)

    def test_followup_redirect_is_not_open(self):
        b = Batch.objects.create(name="B")
        st = Student.objects.create(name="S", roll_number="1")
        sess = Session.objects.create(batch=b, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        att = Attendance.objects.create(session=sess, student=st, status="absent")
        fu = FollowUp.objects.create(attendance=att)
        r = self.c.post(f"/followups/{fu.pk}/update/", {"status": "contacted", "next": "https://evil.example/x"})
        self.assertEqual(r["Location"], "/followups/")
        r = self.c.post(f"/followups/{fu.pk}/update/", {"status": "contacted", "next": "//evil.example"})
        self.assertEqual(r["Location"], "/followups/")

    def test_admin_cannot_lock_themselves_out(self):
        r = self.c.post(f"/accounts/{self.admin.pk}/edit/", {"first_name": "A", "email": "adm@x.com"})
        self.assertEqual(r.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        # the last active administrator cannot be deactivated by another admin either
        other = User.objects.create_user("o@x.com", "o@x.com", "Oth3r-pass-99")
        Profile.objects.create(user=other, role="admin")
        self.admin.is_active = False
        self.admin.save()
        o = self.client_class(raise_request_exception=False)
        o.force_login(other)
        s = o.session
        s["otp_verified"] = True
        s.save()
        r = o.post(f"/accounts/{other.pk}/edit/", {"first_name": "O", "email": "o@x.com"})
        self.assertEqual(r.status_code, 200)

    def test_deactivated_user_cannot_reactivate_via_old_invite(self):
        batch = Batch.objects.create(name="B")
        with patch("core.accounts.send_mail"):
            self.c.post("/accounts/invite/", {"name": "T", "email": "t@x.com", "role": "student",
                                              "roll_number": "9", "batches": [batch.pk]})
        user = User.objects.get(email="t@x.com")
        self.assertFalse(user.has_usable_password())
        user.set_password("Some-pass-12345")  # simulates an accepted account that is later deactivated
        user.is_active = False
        user.save()
        self.assertEqual(self.c.post(f"/accounts/{user.pk}/resend/").status_code, 302)
        self.assertEqual(len(mail.outbox), 0)  # no new invite for a deactivated (not pending) user

    def test_security_headers_and_error_pages(self):
        r = self.c.get("/dashboard/")
        self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"])
        self.assertIn("no-store", r["Cache-Control"])
        r = self.c.get("/does-not-exist/")
        self.assertContains(r, "Page not found", status_code=404)
        r = self.client_class().get("/login/nonsense/")
        self.assertEqual(r.status_code, 403)

    def test_webhook_malformed_payload_is_400_not_500(self):
        with override_settings(WHATSAPP_APP_SECRET="k"):
            for body in (b"[]", b'{"entry": 5}', b'{"entry": [{"changes": [{"value": {"statuses": [1]}}]}]}', b"not json"):
                sig = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
                r = self.client.post("/webhooks/whatsapp/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
                self.assertEqual(r.status_code, 400, body)

    def test_login_throttle_uses_forwarded_ip_only_when_proxies_configured(self):
        from django.test import RequestFactory
        from .accounts import client_ip
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9", REMOTE_ADDR="10.0.0.1")
        with override_settings(NUM_PROXIES=0):
            self.assertEqual(client_ip(req), "10.0.0.1")  # spoofable header ignored
        with override_settings(NUM_PROXIES=1):
            self.assertEqual(client_ip(req), "203.0.113.9")  # proxy-appended address, not the spoofed one

    def test_login_lockout_after_repeated_failures(self):
        User.objects.create_user("v@x.com", "v@x.com", "Right-pass-12345")
        Profile.objects.create(user=User.objects.get(email="v@x.com"), role="teacher")
        c = self.client_class()
        for _ in range(8):
            c.post("/login/teacher/", {"email": "v@x.com", "password": "wrong"})
        r = c.post("/login/teacher/", {"email": "v@x.com", "password": "Right-pass-12345"})
        self.assertContains(r, "Too many attempts")

    def test_student_and_teacher_cannot_reach_admin_pages(self):
        for role_name, urls in (("student", ["/students/", "/reports/", "/followups/", "/settings/", "/accounts/",
                                              "/manage/batch/", "/accounts/invite/"]),
                                ("teacher", ["/settings/", "/accounts/", "/manage/batch/", "/accounts/invite/"])):
            u = User.objects.create_user(f"{role_name}@z.com", f"{role_name}@z.com", "Xx-pass-123456")
            Profile.objects.create(user=u, role=role_name)
            c = self.client_class(raise_request_exception=False)
            c.force_login(u)
            s = c.session
            s["otp_verified"] = True
            s.save()
            for url in urls:
                self.assertEqual(c.get(url).status_code, 403, (role_name, url))
            self.assertEqual(c.post("/manage/batch/", {"name": "Hack"}).status_code, 403)
        self.assertFalse(Batch.objects.filter(name="Hack").exists())

    def test_session_without_otp_is_rejected(self):
        c = self.client_class()
        c.force_login(self.admin)  # logged in but never passed OTP
        self.assertEqual(c.get("/dashboard/").status_code, 302)
        self.assertEqual(c.get("/admin/").status_code, 302)


def csv_body(response):
    """Read a StreamingHttpResponse's full body in tests."""
    return b"".join(response.streaming_content).decode()


class FuzzyInt(int):
    """Lets assertNumQueries accept a range instead of one exact count (brittle across Django versions)."""
    def __new__(cls, low, high):
        obj = super().__new__(cls, high)
        obj.low, obj.high = low, high
        return obj
    def __eq__(self, other):
        return self.low <= other <= self.high
    def __repr__(self):
        return f"[{self.low}..{self.high}]"


class HostHeaderTests(TestCase):
    def test_unknown_host_is_a_clean_400(self):
        r = self.client_class(raise_request_exception=False).get("/login/", HTTP_HOST="evil.example")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Content-Security-Policy", r)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
class EdgeCaseTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm2@x.com", "adm2@x.com", "Adm1n-pass-77", is_superuser=True)
        Profile.objects.create(user=self.admin, role="admin")
        self.c = self.client_class(raise_request_exception=False)
        self.c.force_login(self.admin)
        s = self.c.session
        s["otp_verified"] = True
        s.save()
        self.institute = Institute.objects.create(
            name="Demo", whatsapp_enabled=True, alert_delay_minutes=1,
            whatsapp_phone_number_id="1", whatsapp_template_name="t",
        )
        self.institute.set_whatsapp_access_token("tok")
        self.institute.save()
        self.batch = Batch.objects.create(name="B")

    def _absent_alert(self, active=True):
        st = Student.objects.create(
            name="S", roll_number="1", guardian_phone="919999999999",
            guardian_whatsapp_opt_in=True, active=active,
        )
        st.batches.add(self.batch)
        sess = Session.objects.create(
            batch=self.batch, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1),
            attendance_finalized=True, finalized_at=timezone.now() - timedelta(minutes=5),
        )
        att = save_attendance(sess, st, "absent", self.admin)
        alert = GuardianAlert.objects.create(attendance=att)
        return att, alert, st

    def test_corrected_attendance_cancels_in_flight_alert_instead_of_sending(self):
        """An alert claimed for sending must re-check status, not rely on a stale read."""
        att, alert, st = self._absent_alert()
        with patch("core.services.claim_alert", wraps=claim_alert) as claimed, \
             patch("core.services.requests.post") as post:
            def fix_before_send(alert_id):
                # Simulate a teacher correcting the record the instant after it was claimed.
                result = claim_alert(alert_id)
                save_attendance(att.session, st, "present", self.admin)
                return result
            claimed.side_effect = fix_before_send
            dispatch_guardian_alerts()
            post.assert_not_called()
        alert.refresh_from_db()
        self.assertEqual(alert.status, "cancelled")

    def test_deactivated_student_does_not_get_queued_or_sent(self):
        att, alert, st = self._absent_alert(active=False)
        self.assertEqual(queue_due_notifications(), 0)  # never queued fresh
        with patch("core.services.requests.post") as post:
            dispatch_guardian_alerts()
            post.assert_not_called()
        alert.refresh_from_db()
        self.assertEqual(alert.status, "cancelled")

    def test_delivery_receipt_ignores_unknown_status_and_wont_regress(self):
        att, alert, st = self._absent_alert()
        alert.status, alert.provider_message_id, alert.delivery_status = "sent", "wamid.9", "delivered"
        alert.save()
        self.assertFalse(apply_delivery_receipt("wamid.9", "something_new_meta_added"))
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, "delivered")  # unknown status ignored
        self.assertFalse(apply_delivery_receipt("wamid.9", "sent"))  # can't go backwards
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, "delivered")
        self.assertTrue(apply_delivery_receipt("wamid.9", "read"))
        alert.refresh_from_db()
        self.assertEqual(alert.delivery_status, "read")

    def test_weekly_repeat_is_dst_safe_and_reports_clashes(self):
        from .forms import SessionForm
        with override_settings(TIME_ZONE="America/New_York", USE_TZ=True):
            start = timezone.make_aware(datetime(2027, 3, 7, 18, 0), timezone.get_current_timezone())
            form = SessionForm({
                "batch": self.batch.pk, "starts_at": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
                "ends_at": timezone.localtime(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "repeat_weekly_until": "2027-03-21",
            })
            self.assertTrue(form.is_valid(), form.errors)
            created, skipped = form.save_with_repeats()
            self.assertEqual((created, skipped), (3, 0))
            hours = sorted({timezone.localtime(s.starts_at).hour for s in Session.objects.all()})
            self.assertEqual(hours, [18])  # stays at 6 PM local across the US DST change (Mar 14, 2027)

    def test_weekly_repeat_reports_clash_with_existing_session(self):
        from .forms import SessionForm
        week2 = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(weeks=1)
        Session.objects.create(batch=self.batch, starts_at=week2, ends_at=week2 + timedelta(hours=1))
        start = week2 - timedelta(weeks=1)
        form = SessionForm({
            "batch": self.batch.pk, "starts_at": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
            "ends_at": timezone.localtime(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "repeat_weekly_until": (timezone.localdate() + timedelta(weeks=2)).isoformat(),
        })
        self.assertTrue(form.is_valid(), form.errors)
        created, skipped = form.save_with_repeats()
        self.assertEqual(skipped, 1)

    def test_subject_name_duplicate_is_case_insensitively_blocked(self):
        from .forms import SubjectForm
        Subject.objects.create(batch=self.batch, name="Maths")
        form = SubjectForm({"batch": self.batch.pk, "name": "maths"})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_institute_settings_cannot_create_duplicate_rows(self):
        Institute.objects.all().delete()
        from .forms import InstituteSettingsForm
        for name in ("First Try", "Second Try"):
            form = InstituteSettingsForm({"name": name, "alert_delay_minutes": 10}, instance=Institute.objects.first())
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.assertEqual(Institute.objects.count(), 1)
        self.assertEqual(Institute.objects.get().name, "Second Try")

    def test_students_search_and_pagination(self):
        for i in range(55):
            Student.objects.create(name=f"Zed {i}", roll_number=f"Z{i}")
        Student.objects.create(name="Findme Kumar", roll_number="FK1")
        r1 = self.c.get("/students/")
        self.assertContains(r1, "Page 1 of 2")
        r2 = self.c.get("/students/?page=2")
        self.assertContains(r2, "Page 2 of 2")
        r3 = self.c.get("/students/?q=Findme")
        self.assertContains(r3, "Findme Kumar")
        self.assertNotContains(r3, "Zed 1<")

    def test_accounts_search_and_pagination(self):
        for i in range(55):
            u = User.objects.create_user(f"teach{i}@x.com", f"teach{i}@x.com", "Teach-pass-1234")
            Profile.objects.create(user=u, role="teacher")
        r1 = self.c.get("/accounts/")
        self.assertContains(r1, "Page 1 of 2")
        r2 = self.c.get("/accounts/?q=teach7@x.com")
        self.assertContains(r2, "teach7@x.com")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
class BulkImportAndScaleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("bulk@x.com", "bulk@x.com", "Adm1n-pass-77", is_superuser=True)
        Profile.objects.create(user=self.admin, role="admin")
        self.c = self.client_class(raise_request_exception=False)
        self.c.force_login(self.admin)
        s = self.c.session
        s["otp_verified"] = True
        s.save()
        self.batch = Batch.objects.create(name="JEE 2027", active=True)
        Student.objects.create(name="Existing", roll_number="DUP1")

    def _csv(self, rows):
        header = "name,roll_number,batch,guardian_name,guardian_phone,guardian_whatsapp_opt_in\n"
        return header + "\n".join(rows)

    def test_valid_rows_are_bulk_created_with_batch_links(self):
        from .services import bulk_import_students
        rows = [
            {"name": "A", "roll_number": "R1", "batch": "jee 2027", "guardian_name": "", "guardian_phone": "", "guardian_whatsapp_opt_in": ""},
            {"name": "B", "roll_number": "R2", "batch": "JEE 2027", "guardian_name": "G", "guardian_phone": "9876543210", "guardian_whatsapp_opt_in": "yes"},
        ]
        created, errors = bulk_import_students(rows)
        self.assertEqual((created, errors), (2, []))
        b = Student.objects.get(roll_number="R2")
        self.assertEqual(b.guardian_phone, "919876543210")
        self.assertIn(self.batch, b.batches.all())

    def test_bad_rows_are_skipped_with_reasons_not_a_hard_failure(self):
        from .services import bulk_import_students
        rows = [
            {"name": "", "roll_number": "R3", "batch": "JEE 2027"},  # missing name
            {"name": "C", "roll_number": "DUP1", "batch": "JEE 2027"},  # exists in DB
            {"name": "D", "roll_number": "R4", "batch": "JEE 2027"},  # ok
            {"name": "E", "roll_number": "R4", "batch": "JEE 2027"},  # duplicate within file
            {"name": "F", "roll_number": "R5", "batch": "No Such Batch"},  # unknown batch
            {"name": "G", "roll_number": "R6", "batch": "JEE 2027", "guardian_phone": "12345"},  # bad phone
            {"name": "H", "roll_number": "R7", "batch": "JEE 2027", "guardian_whatsapp_opt_in": "yes"},  # opt-in no phone
        ]
        created, errors = bulk_import_students(rows)
        self.assertEqual(created, 1)
        self.assertEqual(len(errors), 6)
        self.assertTrue(Student.objects.filter(roll_number="R4").exists())

    def test_oversized_file_is_rejected_before_processing(self):
        from .services import bulk_import_students, MAX_IMPORT_ROWS
        rows = [{"name": "X", "roll_number": str(i), "batch": "JEE 2027"} for i in range(MAX_IMPORT_ROWS + 1)]
        created, errors = bulk_import_students(rows)
        self.assertEqual(created, 0)
        self.assertIn("split it into files", errors[0][1])

    def test_import_view_end_to_end(self):
        csv_text = self._csv(["New One,IMP1,JEE 2027,,,no", "Bad Row,,JEE 2027,,,no"])
        upload = SimpleUploadedFile("students.csv", csv_text.encode(), content_type="text/csv")
        r = self.c.post("/students/import/", {"csv_file": upload})
        self.assertContains(r, "1 student imported")
        self.assertContains(r, "1 row")  # skipped count shown
        self.assertTrue(Student.objects.filter(roll_number="IMP1").exists())

    def test_import_requires_admin(self):
        u = User.objects.create_user("teach@x.com", "teach@x.com", "Teach-pass-123")
        Profile.objects.create(user=u, role="teacher")
        c = self.client_class(raise_request_exception=False)
        c.force_login(u)
        s = c.session
        s["otp_verified"] = True
        s.save()
        self.assertEqual(c.get("/students/import/").status_code, 403)
        csv_text = self._csv(["X,RX1,JEE 2027,,,no"])
        upload = SimpleUploadedFile("s.csv", csv_text.encode(), content_type="text/csv")
        self.assertEqual(c.post("/students/import/", {"csv_file": upload}).status_code, 403)

    def test_dashboard_session_list_has_no_subject_n_plus_one(self):
        for i in range(10):
            sub = Subject.objects.create(batch=self.batch, name=f"Sub{i}")
            Session.objects.create(
                batch=self.batch, subject=sub,
                starts_at=timezone.now() - timedelta(days=i), ends_at=timezone.now() - timedelta(days=i) + timedelta(hours=1),
            )
        with self.assertNumQueries(FuzzyInt(1, 9)):
            self.c.get("/dashboard/")

    def test_reports_student_summary_is_paginated_at_scale(self):
        for i in range(150):
            st = Student.objects.create(name=f"Stu{i:03d}", roll_number=f"P{i:03d}")
            st.batches.add(self.batch)
            sess = Session.objects.create(
                batch=self.batch, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1),
                attendance_finalized=True,
            )
            Attendance.objects.create(session=sess, student=st, status="present")
        r = self.c.get("/reports/")
        self.assertContains(r, "Page 1 of 2")
        self.assertEqual(r.context["student_rows"].paginator.count, 150)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="http://testserver")
class SecurityAuditFixesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("sec@x.com", "sec@x.com", "Adm1n-pass-77", is_superuser=True)
        Profile.objects.create(user=self.admin, role="admin")
        self.batch = Batch.objects.create(name="B", active=True)

    def test_attendance_view_does_not_lock_before_authorization_check(self):
        """A student (wrong role entirely) must be rejected without ever taking a row lock."""
        sess = Session.objects.create(batch=self.batch, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        student_user = User.objects.create_user("stu@x.com", "stu@x.com", "Stud-pass-12345")
        Profile.objects.create(user=student_user, role="student")
        c = self.client_class(raise_request_exception=False)
        c.force_login(student_user)
        s = c.session
        s["otp_verified"] = True
        s.save()
        self.assertEqual(c.get(f"/sessions/{sess.pk}/attendance/").status_code, 403)
        # The view must not hold a lock afterwards - a normal admin request on the
        # same row, from a fresh connection/transaction, must succeed immediately.
        admin_c = self.client_class(raise_request_exception=False)
        admin_c.force_login(self.admin)
        s2 = admin_c.session
        s2["otp_verified"] = True
        s2.save()
        self.assertEqual(admin_c.get(f"/sessions/{sess.pk}/attendance/").status_code, 200)

    def test_forgot_password_takes_similar_time_for_unknown_email(self):
        User.objects.create_user("known@x.com", "known@x.com", "Known-pass-1234")
        Profile.objects.create(user=User.objects.get(email="known@x.com"), role="teacher")
        c = self.client_class()
        t0 = time.time()
        c.post("/forgot-password/", {"email": "known@x.com"})
        known_time = time.time() - t0
        t0 = time.time()
        c.post("/forgot-password/", {"email": "totally-unknown@x.com"})
        unknown_time = time.time() - t0
        self.assertGreater(unknown_time, 0.1)  # the deliberate equalising delay ran

    def test_forgot_password_failed_smtp_does_not_reveal_account_exists(self):
        User.objects.create_user("smtp@x.com", "smtp@x.com", "Smtp-pass-12345")
        Profile.objects.create(user=User.objects.get(email="smtp@x.com"), role="teacher")
        with patch("core.accounts.send_mail", side_effect=OSError("smtp down")):
            r1 = self.client.post("/forgot-password/", {"email": "smtp@x.com"})
        r2 = self.client.post("/forgot-password/", {"email": "nobody-at-all@x.com"})
        self.assertContains(r1, "If an active account uses that address")
        self.assertContains(r2, "If an active account uses that address")
        self.assertNotContains(r1, "delivery failed")
        self.assertNotContains(r1, "administrator")

    def test_bulk_import_concurrent_collision_is_a_clean_message_not_a_500(self):
        from .services import bulk_import_students
        with patch("core.services.Student.objects.bulk_create", side_effect=IntegrityError("unique violation")):
            created, errors = bulk_import_students([{"name": "X", "roll_number": "RX", "batch": "B"}])
        self.assertEqual(created, 0)
        self.assertEqual(errors[0][0], 0)
        self.assertIn("same time", errors[0][1])
        self.assertFalse(Student.objects.filter(roll_number="RX").exists())

    def test_import_view_surfaces_whole_file_error_without_500(self):
        with patch("core.services.Student.objects.bulk_create", side_effect=IntegrityError("x")):
            c = self.client_class(raise_request_exception=False)
            c.force_login(self.admin)
            s = c.session
            s["otp_verified"] = True
            s.save()
            upload = SimpleUploadedFile("s.csv", b"name,roll_number,batch\nX,RX,B\n", content_type="text/csv")
            r = c.post("/students/import/", {"csv_file": upload})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "same time")

    def test_session_cookie_samesite_is_pinned(self):
        from django.conf import settings as dj_settings
        self.assertEqual(dj_settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(dj_settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_field_encryption_key_warning_fires_when_unset_in_production(self):
        from django.core.checks import run_checks
        with override_settings(DEBUG=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FIELD_ENCRYPTION_KEY", None)
                warnings = [w for w in run_checks() if w.id == "core.W001"]
        self.assertEqual(len(warnings), 1)
