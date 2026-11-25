import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import Institute, Profile


class Command(BaseCommand):
    help = "First-run setup: create/update the institute and its first administrator."

    def add_arguments(self, parser):
        parser.add_argument("--name", help="Institute name")
        parser.add_argument("--admin-email")
        parser.add_argument("--admin-name", default="Administrator")
        parser.add_argument("--admin-password", help="Omit to be prompted")

    def handle(self, *args, **o):
        User = get_user_model()
        name = o["name"] or input("Institute name: ").strip()
        email = (o["admin_email"] or input("Admin email: ").strip()).lower()
        if not name or not email:
            raise CommandError("Institute name and admin email are required.")
        institute = Institute.objects.first() or Institute()
        institute.name = name
        institute.save()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            password = o["admin_password"] or getpass.getpass("Admin password: ")
            if len(password) < 8:
                raise CommandError("Password must be at least 8 characters.")
            user = User.objects.create_user(
                username=email, email=email, password=password, first_name=o["admin_name"],
                is_staff=True, is_superuser=True,
            )
            self.stdout.write(f"Created administrator {email}.")
        else:
            user.is_staff = user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
            self.stdout.write(f"Promoted existing user {email} to administrator.")
        Profile.objects.update_or_create(user=user, defaults={"role": "admin"})
        self.stdout.write(self.style.SUCCESS(f"Institute '{name}' ready. Sign in via Admin Login."))
