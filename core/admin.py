from django.contrib import admin
from .models import (
    Institute,
    Profile,
    Batch,
    Student,
    Session,
    Attendance,
    AttendanceAudit,
    FollowUp,
    Notification,
    Invitation,
    LoginChallenge,
)

for model in [
    Institute,
    Profile,
    Batch,
    Student,
    Session,
    Attendance,
    AttendanceAudit,
    FollowUp,
    Notification,
    Invitation,
    LoginChallenge,
]:
    admin.site.register(model)
from .models import GuardianAlert

admin.site.register(GuardianAlert)

from .models import Subject
admin.site.register(Subject)
