from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
import csv

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.paginator import Paginator
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_http_methods
from .models import (
    Institute,
    Batch,
    Subject,
    Student,
    Session,
    Attendance,
    FollowUp,
    GuardianAlert,
)
import hashlib
import hmac
import json

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .services import (
    IMPORT_TEMPLATE_CSV,
    apply_delivery_receipt,
    bulk_import_students,
    attendance_summary,
    retry_alert,
    save_attendance,
    save_batch_with_subjects,
    student_summaries,
)
import io


from .accounts import is_admin, role_of
from django.db import transaction
from .access import teacher_can_manage_session, teacher_session_filter
from .forms import BatchForm, InstituteSettingsForm, SessionForm, StudentForm, SubjectForm


def role(request):
    return role_of(request.user)


def allowed(request, *roles):
    return role(request) in roles


def common(request):
    return {"institute": Institute.objects.first(), "role": role(request)}


@login_required
def home(request):
    r = role(request)
    if r == "student":
        student = Student.objects.filter(user=request.user, active=True).first()
        records = (
            Attendance.objects.filter(
                student=student, session__attendance_finalized=True
            ).select_related("session__batch", "session__subject")
            if student
            else Attendance.objects.none()
        )
        upcoming = (
            Session.objects.filter(
                batch__students=student, starts_at__gte=timezone.now()
            ).select_related("batch", "subject").order_by("starts_at")
            if student
            else Session.objects.none()
        )
        return render(
            request,
            "core/student_dashboard.html",
            {
                **common(request),
                "student": student,
                "records": records[:100],
                "counts": records.values("status").annotate(total=Count("id")),
                "summary": attendance_summary(records) if student else None,
                "upcoming": upcoming[:20],
            },
        )
    if r not in ("admin", "teacher"):
        return HttpResponseForbidden()
    sessions = Session.objects.select_related("batch", "subject").order_by("-starts_at")
    if r == "teacher" and not request.user.is_superuser:
        sessions = sessions.filter(teacher_session_filter(request.user)).distinct()
    roster = Student.objects.filter(active=True)
    batches_q = Batch.objects.filter(active=True)
    absences = Attendance.objects.filter(
        status="absent", session__starts_at__date=timezone.localdate()
    )
    follow = FollowUp.objects.exclude(status="resolved")
    if r == "teacher":
        roster = roster.filter(batches__teachers=request.user).distinct()
        batches_q = batches_q.filter(teachers=request.user)
        absences = absences.filter(teacher_session_filter(request.user, "session__")).distinct()
        follow = follow.filter(teacher_session_filter(request.user, "attendance__session__")).distinct()
    data = {
        **common(request),
        "sessions": sessions[:40],
        "student_count": roster.count(),
        "batch_count": batches_q.count(),
        "absent_count": absences.count(),
        "followup_count": follow.count(),
    }
    return render(request, "core/home.html", data)


@login_required
def students(request):
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()

    qs = Student.objects.prefetch_related("batches").order_by("name")

    if role(request) == "teacher":
        qs = qs.filter(batches__teachers=request.user).distinct()

    search = request.GET.get("q", "").strip()[:140]
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(roll_number__icontains=search))

    page_obj = Paginator(qs, settings.LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request, "core/students.html", {**common(request), "students": page_obj, "page_obj": page_obj, "q": search}
    )


@login_required
def batches(request):
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    qs = Batch.objects.prefetch_related("teachers", "subjects").annotate(
        student_count=Count("students", distinct=True)
    )
    if role(request) == "teacher" and not request.user.is_superuser:
        qs = qs.filter(teachers=request.user)
    return render(request, "core/batches.html", {**common(request), "batches": qs})


@login_required
def attendance(request, pk):
    # Plain (unlocked) fetch for the authorization check and for GET: a row lock here
    # would be held for the whole request, including ones we're about to reject, which
    # lets anyone who can merely log in (any role, any session id) stall a real teacher's
    # concurrent attempt to mark/finalize attendance just by holding requests open.
    session = get_object_or_404(Session.objects.select_related("batch"), pk=pk)
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    if (
        role(request) == "teacher"
        and not request.user.is_superuser
        and not teacher_can_manage_session(request.user, session)
    ):
        return HttpResponseForbidden()
    students = list(session.batch.students.filter(active=True).order_by("name"))
    existing = {a.student_id: a.status for a in session.attendance.all()}
    if request.method == "POST":
        if session.attendance_finalized and not allowed(request, "admin"):
            messages.error(
                request, "Finalized attendance can only be corrected by admin."
            )
            return redirect("attendance", pk=pk)
        valid = dict(Attendance.STATUS)
        if any(request.POST.get(f"status_{s.pk}") not in valid for s in students):
            messages.error(request, "Please select a status for every student.")
            return redirect("attendance", pk=pk)
        with transaction.atomic():
            # Lock is taken here, only for an already-authorized write, and only for
            # as long as the actual save takes - not for the whole request/response.
            session = Session.objects.select_for_update().get(pk=session.pk)
            for student in students:
                save_attendance(
                    session, student, request.POST[f"status_{student.pk}"], request.user
                )
            if request.POST.get("finalize") == "yes":
                session.attendance_finalized = True
                session.finalized_at = timezone.now()
                session.save(update_fields=["attendance_finalized", "finalized_at"])
        messages.success(request, "Attendance saved successfully.")
        return redirect("attendance", pk=pk)
    return render(
        request,
        "core/attendance.html",
        {
            **common(request),
            "session": session,
            "rows": [(s, existing.get(s.pk, "")) for s in students],
            "statuses": Attendance.STATUS,
        },
    )


def _followup_scope(request):
    qs = FollowUp.objects.select_related(
        "attendance__student", "attendance__session__batch", "attendance__session__subject",
        "attendance__guardianalert", "assigned_to",
    )
    if role(request) == "teacher" and not request.user.is_superuser:
        qs = qs.filter(teacher_session_filter(request.user, "attendance__session__")).distinct()
    return qs


@login_required
def followups(request):
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    qs = _followup_scope(request).order_by("-updated_at")
    show = request.GET.get("show", "open")
    if show == "open":
        qs = qs.exclude(status="resolved")
    elif show in dict(FollowUp.STATUS):
        qs = qs.filter(status=show)
    return render(
        request,
        "core/followups.html",
        {**common(request), "followups": qs[:200], "show": show},
    )


@login_required
@require_POST
def update_followup(request, pk):
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    item = get_object_or_404(_followup_scope(request), pk=pk)
    status = request.POST.get("status")
    if status not in dict(FollowUp.STATUS):
        return JsonResponse({"error": "Invalid status"}, status=400)
    item.status = status
    item.reason = request.POST.get("reason", "")[:2000]
    item.assigned_to = request.user
    item.save()
    messages.success(request, "Follow-up updated.")
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "followups"
    return redirect(target)


@login_required
@require_POST
def retry_followup_alert(request, pk):
    """Re-queue a failed or cancelled WhatsApp alert for another round of attempts."""
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    item = get_object_or_404(_followup_scope(request), pk=pk)
    alert = getattr(item.attendance, "guardianalert", None)
    student = item.attendance.student
    if not alert:
        if not (student.guardian_whatsapp_opt_in and student.guardian_phone):
            messages.error(request, "Add a guardian phone number and WhatsApp consent on the student record first.")
        elif not item.attendance.session.attendance_finalized:
            messages.error(request, "Finalize this session's attendance to queue the alert.")
        else:
            GuardianAlert.objects.get_or_create(attendance=item.attendance)
            messages.success(request, "Alert queued; it will be sent on the next scheduler run.")
    elif alert.status in ("failed", "cancelled") and retry_alert(alert):
        messages.success(request, "Alert re-queued; it will be sent on the next scheduler run.")
    else:
        messages.error(request, "This alert cannot be retried.")
    return redirect("followups")


class _EchoWriter:
    """A file-like object that returns what it's written instead of buffering it.

    Used with csv.writer so each row can be sent to the client as it's generated
    (StreamingHttpResponse), instead of building the entire CSV in memory first -
    this keeps memory flat and the response starts arriving immediately, however
    many years of attendance history the export covers.
    """

    def write(self, value):
        return value


def _stream_csv(filename, header, rows):
    writer = csv.writer(_EchoWriter())

    def generate():
        yield writer.writerow(header)
        for row in rows:
            yield writer.writerow(row)

    response = StreamingHttpResponse(generate(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def csv_safe(value):
    """Neutralise spreadsheet formulas (=, +, -, @) in exported text."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@login_required
def report_queryset(request):
    """Return only the attendance records the requester is allowed to see."""
    qs = Attendance.objects.select_related("student", "session__batch", "session__subject").order_by(
        "-session__starts_at"
    )
    if role(request) == "teacher" and not request.user.is_superuser:
        qs = qs.filter(teacher_session_filter(request.user, "session__")).distinct()
    batch = request.GET.get("batch")
    status = request.GET.get("status")
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    if batch and batch.isascii() and batch.isdigit() and len(batch) <= 12:
        qs = qs.filter(session__batch_id=int(batch))
    if status in dict(Attendance.STATUS):
        qs = qs.filter(status=status)
    # Malformed dates are ignored instead of crashing the page.
    try:
        start, end = parse_date(date_from or ""), parse_date(date_to or "")
    except ValueError:
        start = end = None
    if start:
        qs = qs.filter(session__starts_at__date__gte=start)
    if end:
        qs = qs.filter(session__starts_at__date__lte=end)
    return qs


@login_required
def reports(request):
    if not allowed(request, "admin", "teacher"):
        return HttpResponseForbidden()
    qs = report_queryset(request)
    visible_batches = Batch.objects.filter(active=True)
    if role(request) == "teacher" and not request.user.is_superuser:
        visible_batches = visible_batches.filter(teachers=request.user)
    if request.GET.get("format") == "csv":
        def rows():
            for item in qs.iterator(chunk_size=500):
                yield [
                    timezone.localtime(item.session.starts_at).strftime("%Y-%m-%d %H:%M"),
                    csv_safe(item.student.name), csv_safe(item.student.roll_number),
                    csv_safe(item.session.batch.name),
                    csv_safe(item.session.subject.name) if item.session.subject_id else "", item.status,
                ]
        return _stream_csv(
            "attendance-report.csv", ["Date", "Student", "Roll number", "Batch", "Subject", "Status"], rows()
        )
    if request.GET.get("format") == "summary_csv":
        def rows():
            for r in student_summaries(qs):
                yield [
                    csv_safe(r["student__roll_number"]), csv_safe(r["student__name"]), r["present"], r["late"],
                    r["absent"], r["excused"], r["total"],
                    "" if r["percentage"] is None else r["percentage"],
                ]
        return _stream_csv(
            "student-attendance-summary.csv",
            ["Roll number", "Student", "Present", "Late", "Absent", "Excused", "Total", "Attendance %"],
            rows(),
        )
    student_page = Paginator(student_summaries(qs), settings.REPORT_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "core/reports.html",
        {
            **common(request),
            "records": qs[:300],
            "counts": qs.order_by().values("status").annotate(total=Count("id")),
            "summary": attendance_summary(qs.order_by()),
            "student_rows": student_page,
            "page_obj": student_page,
            "batches": visible_batches,
            "statuses": Attendance.STATUS,
        },
    )


@login_required
def settings_page(request):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    form = InstituteSettingsForm(request.POST or None, instance=Institute.objects.first())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Institute settings saved.")
        return redirect("settings")
    return render(request, "core/settings.html", {**common(request), "form": form})


def manifest(request):
    institute = Institute.objects.first()
    return JsonResponse(
        {
            "name": institute.name if institute else "Coaching Portal",
            "short_name": "Coaching",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#f7f9ff",
            "theme_color": "#2256d9",
            "icons": [
                {
                    "src": "/static/core/icon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        }
    )


@login_required
def manage(request, kind, pk=None):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    choices = {
        "batch": (Batch, BatchForm),
        "session": (Session, SessionForm),
        "student": (Student, StudentForm),
        "subject": (Subject, SubjectForm),
    }
    if kind not in choices:
        return HttpResponseForbidden()
    model, form_class = choices[kind]
    obj = get_object_or_404(model, pk=pk) if pk else None
    if kind == "student" and not obj:
        return redirect("invite")
    form = form_class(request.POST or None, instance=obj)
    subject_names = (
        request.POST.getlist("subject_names")
        if request.method == "POST"
        else list(obj.subjects.values_list("name", flat=True)) if kind == "batch" and obj else [""]
    )
    if request.method == "POST" and form.is_valid():
        if kind == "batch":
            try:
                save_batch_with_subjects(form, subject_names)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                return redirect("batches")
        elif kind == "session":
            created, skipped = form.save_with_repeats()
            msg = f"{created} class{'es' if created != 1 else ''} scheduled."
            if skipped:
                msg += f" {skipped} repeat{'s' if skipped != 1 else ''} skipped: the batch already had a class at that time."
            messages.success(request, msg)
            return redirect("dashboard")
        else:
            form.save()
            return redirect(
                "students"
                if kind == "student"
                else "batches" if kind in ("batch", "subject") else "dashboard"
            )
    return render(
        request,
        "core/manage_form.html",
        {
            **common(request),
            "form": form,
            "title": ("Edit " if obj else "Create ") + kind,
            "batch_form": kind == "batch",
            "subject_names": subject_names,
        },
    )


@login_required
@require_POST
def delete_item(request, kind, pk):
    """Delete a mutable record, preserving any academic history protected by the DB."""
    if not is_admin(request.user):
        return HttpResponseForbidden()
    choices = {
        "batch": (Batch, "batches"),
        "subject": (Subject, "batches"),
        "student": (Student, "students"),
        "session": (Session, "dashboard"),
    }
    if kind not in choices:
        return HttpResponseForbidden()
    model, destination = choices[kind]
    item = get_object_or_404(model, pk=pk)
    if kind == "session" and item.attendance.exists():
        messages.error(request, "A session with attendance records cannot be deleted.")
        return redirect(destination)
    try:
        item.delete()
    except ProtectedError:
        messages.error(request, "This record has academic history and cannot be deleted.")
    else:
        messages.success(request, f"{kind.title()} deleted.")
    return redirect(destination)


@csrf_exempt
def whatsapp_webhook(request):
    """Meta webhook: GET verifies the subscription; POST delivers status receipts."""
    token = settings.WHATSAPP_VERIFY_TOKEN
    if request.method == "GET":
        if (
            token
            and request.GET.get("hub.mode") == "subscribe"
            and hmac.compare_digest(request.GET.get("hub.verify_token", ""), token)
        ):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden()
    if request.method != "POST":
        return HttpResponse(status=405)
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        return HttpResponseForbidden()  # refuse unsigned receipts
    expected = "sha256=" + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
        return HttpResponseForbidden()
    try:
        payload = json.loads(request.body)
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for st in change.get("value", {}).get("statuses", []):
                    errors = st.get("errors") or [{}]
                    apply_delivery_receipt(
                        str(st.get("id", "")), str(st.get("status", "")), str(errors[0].get("title", ""))
                    )
    except (ValueError, AttributeError, TypeError, IndexError):
        return HttpResponse(status=400)  # malformed payload: never a 500
    return HttpResponse("ok")


# --- Error pages: standalone templates that never touch the database -------------------------
def _error(request, status, title, message):
    return render(request, "core/error.html", {"status": status, "title": title, "message": message}, status=status)


def bad_request(request, exception=None):
    return _error(request, 400, "Bad request", "We could not understand that request.")


def permission_denied(request, exception=None):
    return _error(request, 403, "Not allowed", "You do not have permission to open this page.")


def not_found(request, exception=None):
    return _error(request, 404, "Page not found", "The page you are looking for does not exist.")


def server_error(request):
    return _error(request, 500, "Something went wrong", "An unexpected error occurred. Please try again in a moment.")


def csrf_failure(request, reason=""):
    return _error(request, 403, "Session expired", "Your form expired or the session changed. Go back, refresh the page and try again.")


@login_required
@require_http_methods(["GET", "POST"])
def students_import(request):
    """Bulk-create students from an uploaded CSV. Admin only; see IMPORT_TEMPLATE_CSV
    for the expected columns. Designed for large institutes: thousands of rows are
    validated and written in one request without one database round-trip per row."""
    if not is_admin(request.user):
        return HttpResponseForbidden()
    result = None
    if request.method == "POST":
        upload = request.FILES.get("csv_file")
        if not upload:
            messages.error(request, "Choose a CSV file first.")
        elif upload.size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            messages.error(request, "That file is too large.")
        else:
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                messages.error(request, "The file must be UTF-8 encoded CSV.")
            else:
                reader = csv.DictReader(io.StringIO(text))
                missing = {"name", "roll_number", "batch"} - set(h.strip() for h in (reader.fieldnames or []))
                if missing:
                    messages.error(request, f"Missing required column(s): {', '.join(sorted(missing))}.")
                else:
                    rows = list(reader)
                    created, errors = bulk_import_students(rows)
                    if errors and errors[0][0] == 0:
                        messages.error(request, errors[0][1])  # whole-file error, not a per-row one
                    else:
                        result = {"created": created, "errors": errors[:200], "error_count": len(errors)}
                        if created:
                            messages.success(request, f"{created} student{'s' if created != 1 else ''} imported.")
    return render(request, "core/students_import.html", {**common(request), "result": result})


@login_required
def students_import_template(request):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    response = HttpResponse(IMPORT_TEMPLATE_CSV, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="student-import-template.csv"'
    return response
