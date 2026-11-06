from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('messaging', '0019_video_processing_status'),
        ('groups', '0018_migrate_pending_memberships'),
    ]
    operations = [migrations.AddField(
        model_name='conversation', name='group',
        field=models.OneToOneField(
            blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
            related_name='chat_conversation', to='groups.group',
        ),
    )]
