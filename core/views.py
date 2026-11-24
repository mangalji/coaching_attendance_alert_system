from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count
from django.db.models.deletion import ProtectedError
import csv

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import (
    Institute,
    Batch,
    Subject,
    Student,
    Session,
    Attendance,
    FollowUp,
)
from .services import save_attendance, save_batch_with_subjects

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
            ).select_related("session__batch")
            if student
            else Attendance.objects.none()
        )
        upcoming = (
            Session.objects.filter(
                batch__students=student, starts_at__gte=timezone.now()
            ).order_by("starts_at")
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
                "upcoming": upcoming[:20],
            },
        )
    if r not in ("admin", "teacher"):
        return HttpResponseForbidden()
    sessions = Session.objects.select_related("batch").order_by("-starts_at")
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

    return render(request, "core/students.html", {**common(request), "students": qs})


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
@transaction.atomic
def attendance(request, pk):
    session = get_object_or_404(
        Session.objects.select_for_update().select_related("batch"), pk=pk
    )
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


@login_required
def followups(request):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    qs = FollowUp.objects.select_related(
        "attendance__student", "attendance__session__batch", "attendance__guardianalert"
    ).order_by("-updated_at")
    return render(
        request, "core/followups.html", {**common(request), "followups": qs[:200]}
    )


@login_required
@require_POST
def update_followup(request, pk):
    if not is_admin(request.user):
        return HttpResponseForbidden()
    item = get_object_or_404(FollowUp, pk=pk)
    status = request.POST.get("status")
    if status not in dict(FollowUp.STATUS):
        return JsonResponse({"error": "Invalid status"}, status=400)
    item.status = status
    item.reason = request.POST.get("reason", "")[:2000]
    item.assigned_to = request.user
    item.save()
    messages.success(request, "Follow-up updated.")
    return redirect("followups")


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
    if batch and batch.isdigit():
        qs = qs.filter(session__batch_id=batch)
    if status in dict(Attendance.STATUS):
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(session__starts_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(session__starts_at__date__lte=date_to)
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
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="attendance-report.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Student", "Roll number", "Batch", "Subject", "Status"])
        for item in qs:
            writer.writerow([
                timezone.localtime(item.session.starts_at).strftime("%Y-%m-%d %H:%M"),
                item.student.name, item.student.roll_number, item.session.batch.name,
                item.session.subject.name if item.session.subject_id else "", item.status,
            ])
        return response
    return render(
        request,
        "core/reports.html",
        {
            **common(request),
            "records": qs[:300],
            "counts": qs.values("status").annotate(total=Count("id")),
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
