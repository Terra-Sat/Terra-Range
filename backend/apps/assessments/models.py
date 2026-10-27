from django.db import models


class AssessmentRun(models.Model):
    run_id = models.CharField(max_length=128, unique=True)
    methodology_version = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "assessments"
