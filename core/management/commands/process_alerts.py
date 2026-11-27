import time

from django.core.management.base import BaseCommand

from core.services import dispatch_pending_notifications, purge_expired


class Command(BaseCommand):
    help = (
        "Queue and send due guardian WhatsApp alerts. Run once from a scheduler (cron), "
        "or use --loop to keep a worker process running."
    )

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Keep running instead of exiting.")
        parser.add_argument("--interval", type=int, default=60, help="Seconds between runs with --loop.")

    def handle(self, *args, **options):
        while True:
            try:
                self.stdout.write(str(dispatch_pending_notifications()))
                purge_expired()
            except Exception as exc:  # keep the worker alive across transient DB/network errors
                if not options["loop"]:
                    raise
                self.stderr.write(f"process_alerts error: {exc}")
            if not options["loop"]:
                break
            time.sleep(max(options["interval"], 5))
