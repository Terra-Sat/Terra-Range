# Generated manually for the ClimateIndex model added by ingest_open_data.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("geography", "0005_pixellayer_seasonaloutlook_wardclassshare"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClimateIndex",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=80, unique=True)),
                ("source_file", models.CharField(max_length=120)),
                ("provider", models.CharField(blank=True, max_length=80)),
                # zlib-compressed UTF-8 bytes of the original text file
                ("data_compressed", models.BinaryField()),
                ("raw_bytes", models.PositiveIntegerField()),
                ("compressed_bytes", models.PositiveIntegerField()),
                ("retrieved_at", models.DateField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "app_label": "geography",
            },
        ),
    ]

