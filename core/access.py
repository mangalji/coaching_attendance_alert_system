"""Reusable authorization and queryset scopes for portal views."""

from django.db.models import Q


def teacher_session_filter(user, prefix=""):
    """Return the session scope for a teacher.

    A teacher explicitly assigned to a subject can manage it. When a subject has no
    explicit assignments, the batch-wide teacher assignment applies instead.
    ``prefix`` makes the same rule reusable for Session, Attendance, and FollowUp
    querysets.
    """
    return (
        Q(**{f"{prefix}subject__teachers": user})
        | Q(**{f"{prefix}subject__teachers__isnull": True, f"{prefix}batch__teachers": user})
        | Q(**{f"{prefix}subject__isnull": True, f"{prefix}batch__teachers": user})
    )


def teacher_can_manage_session(user, session):
    """Check a single session using the same assignment rule as list views."""
    if not session.subject_id:
        return session.batch.teachers.filter(pk=user.pk).exists()
    assigned_teachers = session.subject.teachers
    return (
        assigned_teachers.filter(pk=user.pk).exists()
        if assigned_teachers.exists()
        else session.batch.teachers.filter(pk=user.pk).exists()
    )
