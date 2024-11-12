# Generated by Django 4.2.16 on 2024-11-07 15:38

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        (
            "custom_data_fields",
            "0010_customdatafieldsdefinition_profile_required_for_user_type",
        ),
        ("users", "0073_rm_location_from_user_data"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sqluserdata",
            name="profile",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="custom_data_fields.customdatafieldsprofile",
            ),
        ),
    ]
