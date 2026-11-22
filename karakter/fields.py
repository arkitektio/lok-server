import logging
import re

from django.core.exceptions import ValidationError
from django.db import models

logger = logging.getLogger(__name__)


def validate_semver(version: str):
    return True


class VersionField(models.CharField):
    default_validators = [validate_semver]

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)


def validate_app_identifier(name: str):
    return True


class IdentifierField(models.CharField):
    default_validators = [validate_app_identifier]

    def __init__(self, *args, **kwargs):
        kwargs["max_length"] = 1000
        super().__init__(*args, **kwargs)


def validate_s3(value: str) -> None:
    s3_pattern = r"^s3://.+/.+/.+"
    if not re.match(s3_pattern, value):
        raise ValidationError(
            "Invalid S3 path format. Should be s3://datalayer/bucket_name/object_key",
            code="invalid",
        )


class S3Field(models.CharField):
    description = "CharField to store S3 path for Zarr dataset with validation"

    def __init__(self, *args, **kwargs) -> None:
        kwargs["max_length"] = kwargs.get("max_length", 500)

        super().__init__(*args, **kwargs)
