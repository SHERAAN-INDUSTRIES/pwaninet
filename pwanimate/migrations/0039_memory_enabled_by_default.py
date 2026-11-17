from django.db import migrations, models


def enable_memory_for_untouched_preferences(apps, schema_editor):
    SparkMemoryPreference = apps.get_model('pwanimate', 'SparkMemoryPreference')
    # Rows still at the historical implicit default (never explicitly
    # enabled/disabled by the user) are switched to memory-on so important
    # conversations can be captured by default. Explicit choices remain intact.
    SparkMemoryPreference.objects.filter(
        enabled=False,
        consent_version=0,
        revision=0,
    ).update(enabled=True, consent_version=1)


class Migration(migrations.Migration):

    dependencies = [
        ('pwanimate', '0038_canonical_study_binding'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sparkmemorypreference',
            name='enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='sparkmemorypreference',
            name='consent_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(
            enable_memory_for_untouched_preferences,
            migrations.RunPython.noop,
        ),
    ]
