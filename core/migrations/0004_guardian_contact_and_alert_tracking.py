from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0003_batch_subjects")]

    operations = [
        migrations.AddField(
            model_name="student",
            name="guardian_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="student",
            name="guardian_whatsapp_opt_in",
            field=models.BooleanField(
                default=False,
                help_text="Consent has been obtained to send attendance alerts by WhatsApp.",
            ),
        ),
        migrations.AlterField(
            model_name="guardianalert",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("sending", "Sending"),
                    ("sent", "Sent"),
                    ("failed", "Needs review"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="guardianalert",
            name="attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="guardianalert",
            name="last_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="guardianalert",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="guardianalert",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
