from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pwanimate", "0015_rename_pwanimate_m_user_id_6af0dc_idx_pwanimate_p_user_id_1dd5d9_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pwanimatepreferences",
            name="auto_tts_enabled",
            field=models.BooleanField(default=False, help_text="Automatically read new Spark replies aloud"),
        ),
        migrations.AddField(
            model_name="pwanimatepreferences",
            name="voice_gender",
            field=models.CharField(
                choices=[("female", "Female"), ("male", "Male")],
                default="female",
                help_text="Preferred Spark speech voice gender",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pwanimatepreferences",
            name="voice_style",
            field=models.CharField(
                choices=[("warm", "Warm Guide"), ("academic", "Focused Academic")],
                default="warm",
                help_text="Preferred Spark speech style",
                max_length=20,
            ),
        ),
    ]
