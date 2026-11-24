from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [("core", "0005_operational_indexes_and_session_constraint")]

    operations = [
        migrations.AlterField(
            model_name="institute",
            name="alert_delay_minutes",
            field=models.PositiveIntegerField(
                default=10,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(1440),
                ],
            ),
        ),
    ]
