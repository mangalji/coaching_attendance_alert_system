from django.urls import path
from django.contrib.auth import views as auth_views
from . import views, accounts

urlpatterns = [
    path("", accounts.landing, name="home"),
    path("dashboard/", views.home, name="dashboard"),
    path("login/", accounts.landing, name="login"),
    path("login/<str:portal>/", accounts.sign_in, name="portal_login"),
    path("verify/", accounts.otp, name="otp"),
    path("invite/<str:token>/", accounts.accept, name="accept_invite"),
    path("accounts/", accounts.accounts, name="accounts"),
    path("accounts/invite/", accounts.invite, name="invite"),
    path("accounts/<int:pk>/resend/", accounts.resend, name="resend"),
    path("accounts/<int:pk>/edit/", accounts.edit_account, name="edit_account"),
    path("accounts/<int:pk>/delete/", accounts.delete_account, name="delete_account"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("students/", views.students, name="students"),
    path("batches/", views.batches, name="batches"),
    path("sessions/<int:pk>/attendance/", views.attendance, name="attendance"),
    path("followups/", views.followups, name="followups"),
    path("followups/<int:pk>/update/", views.update_followup, name="update_followup"),
    path("reports/", views.reports, name="reports"),
    path("settings/", views.settings_page, name="settings"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
]

urlpatterns += [
    path("manage/<str:kind>/", views.manage, name="manage"),
    path("manage/<str:kind>/<int:pk>/", views.manage, name="manage_edit"),
    path("manage/<str:kind>/<int:pk>/delete/", views.delete_item, name="delete_item"),
]
