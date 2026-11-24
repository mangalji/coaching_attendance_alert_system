from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_alter_institute_alert_delay_minutes")]

    operations = [
        migrations.AddField(
            model_name="institute",
            name="whatsapp_access_token_encrypted",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="institute",
            name="whatsapp_graph_version",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="institute",
            name="whatsapp_phone_number_id",
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name="institute",
            name="whatsapp_template_language",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="institute",
            name="whatsapp_template_name",
            field=models.CharField(blank=True, max_length=180),
        ),
    ]
