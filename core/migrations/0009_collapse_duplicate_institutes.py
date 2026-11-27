from django.db import migrations


def collapse(apps, schema_editor):
    """If more than one Institute row ever got created (a pre-fix race condition),
    keep the earliest and drop the rest so the new pk=1 singleton write doesn't collide."""
    Institute = apps.get_model("core", "Institute")
    keep = Institute.objects.order_by("pk").first()
    if keep:
        Institute.objects.exclude(pk=keep.pk).delete()
        if keep.pk != 1:
            keep.pk = 1
            keep.save()


class Migration(migrations.Migration):
    dependencies = [("core", "0008_alert_retry_and_delivery")]
    operations = [migrations.RunPython(collapse, migrations.RunPython.noop)]
