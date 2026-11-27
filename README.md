# Smart Coaching Institute Management & Attendance System

A reusable, responsive **single-institute** Django application that digitizes daily academic operations. It is installable as a browser PWA on supported Android and iOS devices; it is not a native Android/iOS application.

## Included
- Configurable institute identity, contact details, alert delay, and WhatsApp enable toggle
- Three email-OTP protected portals: administrator, teacher, and student
- Secure invitation-based account creation with expiring one-time password setup links
- Batch, subject, teacher assignment, student enrollment, and class scheduling management
- Attendance draft/finalize controls, audit records, role-scoped permissions, and student attendance history
- Filterable reports with server-generated CSV downloads
- Automatic absence follow-ups with notes, ownership, resolution, and WhatsApp delivery state
- Consent-aware WhatsApp Cloud API notifications via a scheduled management command
- Configurable SQLite, PostgreSQL, or MySQL database support
- Responsive mobile web UI and installable PWA manifest

## Quick start (Windows PowerShell)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py setup_institute   # institute name + first administrator
python manage.py runserver
```
Open http://127.0.0.1:8000. Sign in through **Admin Login** with the email/password you gave `setup_institute` (a 6-digit OTP is emailed; with the default console email backend it prints in the terminal), then set branding in **Institute Settings**. Use **Team & accounts** to invite teachers, students, or additional administrators. Create batches and subjects in **Batches**, then schedule classes from the dashboard.

## Linux / macOS
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py setup_institute
python manage.py runserver
```

## WhatsApp automation
1. Obtain Meta Cloud API credentials and an approved template named as `WHATSAPP_TEMPLATE_NAME`. The supplied template uses student name, batch, class date, and institute name parameters.
2. Obtain opt-in consent and configure guardian name, E.164 phone number, and WhatsApp opt-in on each student record.
3. Set credentials in .env, enable WhatsApp in Institute Settings.
4. Run `python manage.py process_alerts` every 1-5 minutes via an **external scheduler** (cron / Task Scheduler), or keep one worker alive with `python manage.py process_alerts --loop --interval 60` (this is what `docker-compose.yml` does). It queues finalized absences after the configured delay and sends due alerts.
5. Alert status is visible in **Follow-ups** as Pending, Sent, Needs review, or Cancelled. Failed sends retry automatically (after 5 and 30 minutes, 3 attempts); admins and teachers can **Retry alert** from Follow-ups. For delivered/read receipts, set `WHATSAPP_VERIFY_TOKEN` and `WHATSAPP_APP_SECRET` and register `PUBLIC_BASE_URL/webhooks/whatsapp/` as the webhook callback in Meta (subscribe to *messages*).

Administrators can also maintain the WhatsApp access token, phone number ID, template, language, and Graph API version in **Institute Settings → WhatsApp integration**. The access token is encrypted at rest; set `FIELD_ENCRYPTION_KEY` in production so it remains independent of the Django secret key.

## What each role can do
- **Administrator**: invite/edit/delete users, batches, subjects, teacher assignments, enrollments, schedule classes (including weekly repeats), correct finalized attendance, follow-ups, reports/CSV, institute and WhatsApp settings.
- **Teacher**: mark/finalize attendance for assigned batches/subjects, see their students, manage follow-ups and retry alerts for their sessions, reports for their batches.
- **Student**: own schedule, attendance history and attendance percentage.
- Everyone: email-OTP sign-in per portal and self-service *Forgot password*.

## Docker (one stack per institute)
Copy `.env.example` to `.env`, point `DATABASE_URL` at PostgreSQL/MySQL, then `docker compose up -d`; run `docker compose exec web python manage.py setup_institute` once.

## Production deployment
- Set `DJANGO_DEBUG=False`, secure `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, a database connection, and HTTPS.
- Run `python manage.py migrate`, `python manage.py collectstatic --noinput` and `gunicorn config.wsgi:application`.
- Use a reliable always-on host or managed scheduled job for alerts. Free sleeping instances are unsuitable for guaranteed notification delivery.
- Configure HTTPS before using PWA installation on real phones.
- Back up the database, restrict admin access, set retention policies and capture guardian consent.

## Database configuration

The app uses SQLite by default. Set `DATABASE_URL` in `.env` for the selected provider:

```dotenv
# SQLite
DATABASE_URL=sqlite:///db.sqlite3

# PostgreSQL
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE

# MySQL
DATABASE_URL=mysql://USER:PASSWORD@HOST:3306/DATABASE
```

Alternatively, remove `DATABASE_URL` and use `DATABASE_ENGINE` (`sqlite`, `postgresql`, or `mysql`) together with `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_HOST`, and `DATABASE_PORT`. `DATABASE_CONN_MAX_AGE` controls persistent connections. Run migrations after switching providers.

## Deployment scope
This is intentionally a configurable single-institute deployment: branding and behavior are stored in the database while API secrets remain in environment variables. Billing, native mobile binaries, multi-institute tenancy and offline synchronization are separate product expansions; run one deployment (own database and `.env`) per institute.

## Tests
`python manage.py test`

## Recommended next steps
For larger deployments, add bulk student import, monitored background workers, and an automated backup policy.

## Production checklist
1. `.env`: `DJANGO_DEBUG=False`, a long random `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=your.domain`, `CSRF_TRUSTED_ORIGINS=https://your.domain`, `PUBLIC_BASE_URL=https://your.domain`.
2. Serve over HTTPS only (cookies are `Secure`). Behind a proxy set `SECURE_PROXY_SSL_HEADER=True`, `FORCE_HTTPS=True` and `NUM_PROXIES=1` (otherwise login throttling sees only the proxy's IP).
3. Use PostgreSQL/MySQL (`DATABASE_URL`) and back it up. Set `FIELD_ENCRYPTION_KEY` (changing `DJANGO_SECRET_KEY` without it would make the stored WhatsApp token unreadable).
4. Configure real SMTP (`EMAIL_*`): OTP, invitations and password resets are all emailed.
5. Run `python manage.py collectstatic --noinput` **with the same `DJANGO_DEBUG` value the app will run with** (mixing them is a known gotcha: the app falls back to unhashed/stale static files rather than crashing, but will warn in logs). Then `migrate`, `setup_institute`, and `python manage.py check --deploy`.
6. Keep one alert worker running (`process_alerts --loop`); it also purges expired OTP/throttle/invite rows.
7. `/admin/` is disabled unless `DJANGO_ADMIN_ENABLED=True`. Errors are logged to stdout (`LOG_LEVEL`).

## Scaling to a large institute (thousands of students)
- **Bulk-add students**: Students page → "Bulk import (CSV)" (admin only). Upload `name, roll_number, batch` (+ optional guardian columns); a sample file is one click away on that page. This writes the whole file in two database statements total, so importing thousands of rows takes well under a second — tested with 5,000 rows. It only creates student/attendance records, not portal logins (creating thousands of login invitations in one request would make that request itself time out); invite individual students to the portal afterwards the normal way.
- **List and report pages stay at a fixed number of queries** regardless of institute size (Students, Team & accounts, and the per-student report table are paginated, `LIST_PAGE_SIZE`/`REPORT_PAGE_SIZE` env vars tune the page size). Tested at 5,000 students / 300,000 attendance rows: each page loads in well under half a second.
- **CSV exports stream** row by row instead of building the file in memory, so a full multi-month export doesn't spike server memory or make the browser wait for the whole file before the download starts. For an export spanning years, prefer filtering by date range first, or use the "Student summary CSV" (aggregated, far smaller) for an institute-wide view.
- For very large exports behind a reverse proxy, raise the proxy's read timeout for the `/reports/` path (a multi-year, full-detail export can run for tens of seconds).
- Use PostgreSQL or MySQL in production (not SQLite) — see `DATABASE_URL` in `.env.example`; `DATABASE_CONN_MAX_AGE` controls connection pooling.

## Extending the system (architecture for future features)
- `core/models.py` — data model. `core/forms.py` — validation and input shaping. `core/services.py` — business logic (attendance, alerts, reports, bulk import) that views call into; put new domain logic here, not in views, so it stays testable and reusable (e.g. from a management command).
- `core/views.py` / `core/accounts.py` — HTTP layer: authorization checks, then delegate to `services.py`.
- A **new report**: add a query helper in `services.py` (see `attendance_summary`, `student_summaries`), then a view branch and a template section; reuse `_stream_csv` for any CSV download so large exports stay memory-safe automatically.
- A **new notification channel** (e.g. SMS, email-to-parent) alongside WhatsApp: model it on `GuardianAlert`/`dispatch_guardian_alerts` — a queued row, a claim-then-send step, retry/backoff, and a management command run by the scheduler. Keep sending logic out of request/view code so a slow provider can never block a page load.
- A **new role or portal**: `Profile.role` plus `core/access.py`'s scope helpers (`teacher_session_filter`) are the two places role-based visibility is defined; most views and templates branch on `role` consistently with this pattern.
- A **new bulk-data entry point** (e.g. bulk teacher assignment): follow `bulk_import_students`'s shape — pre-fetch lookups once, validate in Python, write with `bulk_create`/bulk M2M `through` rows, return `(created, errors)` rather than failing the whole file on one bad row.
