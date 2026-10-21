from django.db import migrations


def seed(apps, schema_editor):
    Campaign = apps.get_model("checker", "Campaign")
    for campaign_id, label in [("21625", "D27 (1)"), ("21627", "D27 (2)")]:
        Campaign.objects.get_or_create(campaign_id=campaign_id, defaults={"label": label})


class Migration(migrations.Migration):
    dependencies = [("checker", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
