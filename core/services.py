"""Attendance state changes and the scheduled WhatsApp outbox."""

import os
from datetime import timedelta

import requests
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Attendance, AttendanceAudit, FollowUp, GuardianAlert, Institute, Notification, Subject


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
    existing = set()
    if form.instance.pk:
        existing = set(form.instance.subjects.values_list("name", flat=True))
    if not existing.issubset(normalized):
        raise ValueError("Existing subjects cannot be removed or renamed because they may have class history.")
    with transaction.atomic():
        batch = form.save()
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
    return GuardianAlert.objects.filter(
        pk=alert_id, status__in=["pending", "failed"], attempts__lt=3
    ).update(status="sending", attempts=F("attempts") + 1)


def dispatch_guardian_alerts(limit=50):
    """Send queued alerts. Run from one recurring scheduler, never a web request."""
    institute = Institute.objects.first()
    if not institute or not institute.whatsapp_enabled:
        return {"sent": 0, "failed": 0, "skipped": "WhatsApp alerts are disabled"}
    provider = whatsapp_settings(institute)
    if not provider["token"] or not provider["phone_id"]:
        return {"sent": 0, "failed": 0, "skipped": "Missing WhatsApp credentials"}

    result = {"sent": 0, "failed": 0}
    alerts = (
        GuardianAlert.objects.filter(status__in=["pending", "failed"], attempts__lt=3)
        .select_related("attendance__student", "attendance__session__batch")
        .order_by("created_at")[:limit]
    )
    for alert in alerts:
        attendance, student = alert.attendance, alert.attendance.student
        if not claim_alert(alert.pk):
            continue
        if attendance.status != "absent" or not student.guardian_whatsapp_opt_in:
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
            alert.last_error = ""
            result["sent"] += 1
        except (requests.RequestException, ValueError, IndexError, KeyError) as exc:
            alert.status, alert.last_error = "failed", str(exc)[:1000]
            result["failed"] += 1
        alert.save(update_fields=["status", "sent_at", "provider_message_id", "last_error"])
    return result


def dispatch_pending_notifications(limit=50):
    """Backward-compatible entry point for installations using the original command API."""
    queue_due_notifications()
    return dispatch_guardian_alerts(limit)
