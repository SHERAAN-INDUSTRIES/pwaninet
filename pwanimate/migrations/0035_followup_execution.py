from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('pwanimate', '0034_spark_followup')]
    operations = [
        migrations.AddField('sparkfollowup', 'execution', models.JSONField(default=dict, blank=True)),
        migrations.AddField('sparkfollowup', 'claimed_at', models.DateTimeField(null=True, blank=True)),
        migrations.AddField('sparkfollowup', 'execution_run', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='pwanimate.sparkagentrun')),
        migrations.AlterField('sparkfollowup', 'status', models.CharField(max_length=16, db_index=True, default='pending', choices=[('pending','Pending'),('executing','Executing'),('failed','Failed'),('interrupted','Interrupted'),('consumed','Consumed'),('expired','Expired'),('cancelled','Cancelled')])),
    ]
