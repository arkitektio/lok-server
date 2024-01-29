from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


from django.contrib.auth import get_user_model
from django.db import models

# Create your models here.


class Comment(models.Model):
    identifier = models.CharField(max_length=1000)
    object = models.PositiveIntegerField()
    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="comments",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    descendents = models.JSONField(default=list)
    mentions = models.ManyToManyField(
        get_user_model(), blank=True, related_name="mentioned_in"
    )
    resolved = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="resolved_comments",
    )


import komment.signals
