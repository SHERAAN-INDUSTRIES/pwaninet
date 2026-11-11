from django.db import migrations


def backfill(apps, schema_editor):
    Call = apps.get_model('calling', 'Call')
    Message = apps.get_model('messaging', 'Message')
    alias = schema_editor.connection.alias
    labels = {'ended': 'Voice call', 'missed': 'Missed voice call',
              'declined': 'Declined voice call', 'cancelled': 'Cancelled voice call'}
    for call in Call.objects.using(alias).filter(history_message__isnull=True, status__in=labels).iterator():
        message = Message.objects.using(alias).create(conversation_id=call.conversation_id,
            sender_id=call.caller_id, message_type='voice_call', content=labels[call.status])
        Message.objects.using(alias).filter(pk=message.pk).update(created_at=call.ended_at or call.created_at)
        Call.objects.using(alias).filter(pk=call.pk).update(history_message_id=message.pk)


class Migration(migrations.Migration):
    dependencies = [('calling', '0002_call_history_message')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
