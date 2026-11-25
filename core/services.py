"""Attendance state changes and the scheduled WhatsApp outbox."""

import os
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Attendance, AttendanceAudit, FollowUp, GuardianAlert, Institute, Notification, Subject


MAX_ALERT_ATTEMPTS = 3
RETRY_BACKOFF_MINUTES = (5, 30)  # wait before attempt 2, attempt 3
STALE_CLAIM_MINUTES = 10


def normalize_phone(raw):
    """Return digits-only international number (WhatsApp format) or raise ValidationError.

    10-digit local numbers get DEFAULT_COUNTRY_CODE; leading 0 / + / spaces are removed.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not digits.isdigit():
        raise ValidationError("Enter a valid phone number, e.g. +919876543210.")
    if len(digits) == 10:
        digits = settings.DEFAULT_COUNTRY_CODE + digits
    if not 11 <= len(digits) <= 15:
        raise ValidationError("Phone number must include the country code (11-15 digits).")
    return digits


def attendance_summary(queryset):
    """Counts and attendance percentage for an Attendance queryset.

    Present and late count as attended; excused sessions are left out of the percentage.
    """
    counts = {row["status"]: row["n"] for row in queryset.values("status").annotate(n=Count("id"))}
    total = sum(counts.values())
    countable = total - counts.get("excused", 0)
    attended = counts.get("present", 0) + counts.get("late", 0)
    return {
        "counts": counts,
        "total": total,
        "percentage": round(100 * attended / countable, 1) if countable else None,
    }


def student_summaries(queryset):
    """Per-student rows (name, roll, counts, %) from an Attendance queryset."""
    rows = (
        queryset.order_by()
        .values("student_id", "student__name", "student__roll_number")
        .annotate(
            present=Count("id", filter=Q(status="present")),
            absent=Count("id", filter=Q(status="absent")),
            late=Count("id", filter=Q(status="late")),
            excused=Count("id", filter=Q(status="excused")),
            total=Count("id"),
        )
        .order_by("student__name")
    )
    out = []
    for r in rows:
        countable = r["total"] - r["excused"]
        r["percentage"] = (
            round(100 * (r["present"] + r["late"]) / countable, 1) if countable else None
        )
        out.append(r)
    return out


def save_attendance(session, student, status, actor):
    """Save one student's status, retaining a complete audit trail."""
    with transaction.atomic():
        record, created = Attendance.objects.select_for_update().get_or_create(
            session=session, student=student, defaults={"status": status, "marked_by": actor}
        )
        old = "" if created else record.status
        if created or old != status:
            if not created:
                record.status = status
                record.marked_by = actor
                record.save(update_fields=["status", "marked_by", "updated_at"])
            AttendanceAudit.objects.create(
                attendance=record, old_status=old, new_status=status, actor=actor
            )
            if status == "absent":
                FollowUp.objects.get_or_create(attendance=record)
            else:
                FollowUp.objects.filter(attendance=record).update(status="resolved")
                GuardianAlert.objects.filter(
                    attendance=record, status__in=["pending", "failed"]
                ).update(status="cancelled")
                Notification.objects.filter(attendance=record, status="pending").update(status="cancelled")
    return record


def save_batch_with_subjects(form, subject_names):
    """Persist a batch and its additive subject list in one transaction."""
    normalized = [name.strip() for name in subject_names if name.strip()]
    if not normalized or any(len(name) > 140 for name in normalized):
        raise ValueError("Enter at least one subject, each no longer than 140 characters.")
    if len({name.casefold() for name in normalized}) != len(normalized):
        raise ValueError("Subject names must be unique within a batch.")
    removed = []
    existing = set()
    if form.instance.pk:
        current = list(form.instance.subjects.all())
        existing = {sub.name for sub in current}
        wanted = {n.casefold() for n in normalized}
        removed = [sub for sub in current if sub.name.casefold() not in wanted]
        # keep the stored spelling for subjects that were merely retyped in another case
        normalized = [
            next((sub.name for sub in current if sub.name.casefold() == n.casefold()), n)
            for n in normalized
        ]
        blocked = [sub.name for sub in removed if sub.session_set.exists()]
        if blocked:
            raise ValueError(
                "Cannot remove subjects that already have classes scheduled: " + ", ".join(blocked)
            )
    with transaction.atomic():
        batch = form.save()
        for sub in removed:
            sub.delete()
        Subject.objects.bulk_create(
            [Subject(batch=batch, name=name) for name in normalized if name not in existing],
            ignore_conflicts=True,
        )
    return batch


def queue_due_notifications():
    """Create idempotent outbox records after the institute's configured delay."""
    institute = Institute.objects.first()
    if not institute or not institute.whatsapp_enabled:
        return 0
    cutoff = timezone.now() - timedelta(minutes=institute.alert_delay_minutes)
    records = (
        Attendance.objects.filter(
            status="absent", session__attendance_finalized=True,
            session__finalized_at__lte=cutoff, student__guardian_phone__gt="",
            student__guardian_whatsapp_opt_in=True,
        )
        .select_related("student", "session__batch")
        .exclude(guardianalert__isnull=False)
    )
    count = 0
    for record in records.iterator(chunk_size=200):
        _, created = GuardianAlert.objects.get_or_create(attendance=record)
        count += int(created)
    return count


def whatsapp_settings(institute):
    """Return database settings first, falling back to environment configuration."""
    return {
        "token": institute.get_whatsapp_access_token() or os.getenv("WHATSAPP_ACCESS_TOKEN"),
        "phone_id": institute.whatsapp_phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
        "template": institute.whatsapp_template_name or os.getenv("WHATSAPP_TEMPLATE_NAME", "attendance_absent_alert"),
        "language": institute.whatsapp_template_language or os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_IN"),
        "api_version": institute.whatsapp_graph_version or os.getenv("WHATSAPP_GRAPH_VERSION", "v25.0"),
    }


def absence_alert_payload(attendance, institute, provider):
    """Build the approved WhatsApp template payload for one absence."""
    student = attendance.student
    return {
        "messaging_product": "whatsapp",
        "to": student.guardian_phone,
        "type": "template",
        "template": {
            "name": provider["template"],
            "language": {"code": provider["language"]},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": student.name},
                {"type": "text", "text": attendance.session.batch.name},
                {"type": "text", "text": timezone.localtime(attendance.session.starts_at).strftime("%d %B %Y")},
                {"type": "text", "text": institute.name},
            ]}],
        },
    }


def claim_alert(alert_id):
    """Atomically claim an alert, preventing duplicate sends from concurrent jobs."""
    now = timezone.now()
    return GuardianAlert.objects.filter(
        pk=alert_id, status__in=["pending", "failed"], attempts__lt=MAX_ALERT_ATTEMPTS
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)).update(
        status="sending", attempts=F("attempts") + 1, claimed_at=now
    )


def recover_stale_claims():
    """Return alerts stuck in 'sending' (worker crashed mid-send) to the queue."""
    cutoff = timezone.now() - timedelta(minutes=STALE_CLAIM_MINUTES)
    return GuardianAlert.objects.filter(status="sending", claimed_at__lt=cutoff).update(
        status="failed", last_error="Interrupted before a result was recorded; will retry."
    )


def _error_text(exc):
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            detail = response.json().get("error", {}).get("message")
        except ValueError:
            detail = None
        if detail:
            return f"HTTP {response.status_code}: {detail}"[:1000]
    return str(exc)[:1000]


def retry_alert(alert):
    """Admin action: give a failed/cancelled alert a fresh set of attempts."""
    att = alert.attendance
    if att.status != "absent" or not att.student.guardian_whatsapp_opt_in:
        return False
    alert.status, alert.attempts, alert.next_attempt_at, alert.last_error = "pending", 0, None, ""
    alert.save(update_fields=["status", "attempts", "next_attempt_at", "last_error"])
    return True


def dispatch_guardian_alerts(limit=50):
    """Send queued alerts. Run from one recurring scheduler, never a web request."""
    institute = Institute.objects.first()
    if not institute or not institute.whatsapp_enabled:
        return {"sent": 0, "failed": 0, "skipped": "WhatsApp alerts are disabled"}
    provider = whatsapp_settings(institute)
    if not provider["token"] or not provider["phone_id"]:
        return {"sent": 0, "failed": 0, "skipped": "Missing WhatsApp credentials"}

    recover_stale_claims()
    result = {"sent": 0, "failed": 0}
    now = timezone.now()
    alerts = (
        GuardianAlert.objects.filter(status__in=["pending", "failed"], attempts__lt=MAX_ALERT_ATTEMPTS)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .select_related("attendance__student", "attendance__session__batch")
        .order_by("created_at")[:limit]
    )
    for alert in alerts:
        attendance, student = alert.attendance, alert.attendance.student
        if not claim_alert(alert.pk):
            continue
        alert.refresh_from_db()
        if attendance.status != "absent" or not student.guardian_whatsapp_opt_in or not student.guardian_phone:
            alert.status = "cancelled"
            alert.save(update_fields=["status"])
            continue
        payload = absence_alert_payload(attendance, institute, provider)
        try:
            response = requests.post(
                f"https://graph.facebook.com/{provider['api_version']}/{provider['phone_id']}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {provider['token']}"},
                timeout=15,
            )
            response.raise_for_status()
            alert.status, alert.sent_at = "sent", timezone.now()
            alert.provider_message_id = response.json().get("messages", [{}])[0].get("id", "")
            alert.delivery_status = "sent"
            alert.last_error = ""
            result["sent"] += 1
        except (requests.RequestException, ValueError, IndexError, KeyError) as exc:
            alert.status, alert.last_error = "failed", _error_text(exc)
            if alert.attempts < MAX_ALERT_ATTEMPTS:
                wait = RETRY_BACKOFF_MINUTES[min(alert.attempts - 1, len(RETRY_BACKOFF_MINUTES) - 1)]
                alert.next_attempt_at = timezone.now() + timedelta(minutes=wait)
            result["failed"] += 1
        alert.save(update_fields=[
            "status", "sent_at", "provider_message_id", "last_error", "next_attempt_at", "delivery_status",
        ])
    return result


def apply_delivery_receipt(message_id, status, error=""):
    """Record a Meta delivery receipt (sent/delivered/read/failed) for an alert."""
    alert = GuardianAlert.objects.filter(provider_message_id=message_id).first()
    if not alert:
        return False
    order = {"sent": 1, "delivered": 2, "read": 3}
    if status == "failed":
        alert.delivery_status, alert.status = "failed", "failed"
        alert.last_error = (error or "Delivery failed")[:1000]
    elif order.get(status, 0) >= order.get(alert.delivery_status, 0):
        alert.delivery_status = status
    alert.save(update_fields=["delivery_status", "status", "last_error"])
    return True


def dispatch_pending_notifications(limit=50):
    """Backward-compatible entry point for installations using the original command API."""
    queue_due_notifications()
    return dispatch_guardian_alerts(limit)
