from django.conf import settings
import hashlib
from django.core.cache import cache
from storages.backends.s3boto3 import S3Boto3Storage


class PrivateMediaStorage(S3Boto3Storage):
    location = "logos"
    default_acl = "private"
    file_overwrite = False

    def url(self, name):
        # Add a prefix to avoid conflicts with any other apps
        key = f"PrivateMediaStorage_{name}"
        result = cache.get(key)
        if result:
            return result

        # No cached value exists, follow the usual logic
        result = super(PrivateMediaStorage, self).url(name)
        result = result.replace(settings.AWS_S3_ENDPOINT_URL, "")

        # Cache the result for 3/4 of the temp_url's lifetime.
        try:
            timeout = settings.AWS_QUERYSTRING_EXPIRE
        except:
            timeout = 3600
        timeout = int(timeout * 0.75)
        cache.set(key, result, timeout)

        return result
