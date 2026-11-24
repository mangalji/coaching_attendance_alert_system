from django.core.management.base import BaseCommand
from core.services import dispatch_pending_notifications


class Command(BaseCommand):
    help = "Queue and send due guardian WhatsApp alerts; run periodically using one scheduler."

    def handle(self, *args, **kwargs):
        self.stdout.write(str(dispatch_pending_notifications()))
