from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

def migrate_existing(apps, schema_editor):
    Batch = apps.get_model('core', 'Batch')
    Subject = apps.get_model('core', 'Subject')
    for batch in Batch.objects.all():
        subject = Subject.objects.create(batch=batch, name=batch.subject.strip() or 'General')
        if batch.teacher_id:
            batch.teachers.add(batch.teacher_id)
            subject.teachers.add(batch.teacher_id)
        batch.sessions.update(subject=subject)

class Migration(migrations.Migration):
    dependencies = [('core', '0002_auththrottle_student_guardian_phone_student_user_and_more'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AlterField(model_name="batch", name="teacher", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="legacy_teaching_batches", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='batch', name='teachers', field=models.ManyToManyField(blank=True, related_name='teaching_batches', to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name='Subject', fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('name', models.CharField(max_length=140)), ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subjects', to='core.batch')), ('teachers', models.ManyToManyField(blank=True, related_name='teaching_subjects', to=settings.AUTH_USER_MODEL))], options={'constraints':[models.UniqueConstraint(fields=('batch','name'), name='unique_batch_subject')]}),
        migrations.AddField(model_name='session', name='subject', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.subject')),
        migrations.RunPython(migrate_existing),
        migrations.RemoveField(model_name='batch', name='teacher'),
        migrations.RemoveField(model_name='batch', name='subject'),
    ]