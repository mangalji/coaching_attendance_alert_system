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
