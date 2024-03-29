from urllib.parse import urlparse

from django.core.exceptions import DisallowedRedirect
from django.http import HttpResponse
from django.utils.encoding import iri_to_uri


class ConfigureResponseRedirect(HttpResponse):
    """
    An HTTP 302 redirect with an explicit list of allowed schemes.
    Works like django.http.HttpResponseRedirect but we customize it
    to give us more flexibility on allowed scheme validation.
    """

    status_code = 302

    def __init__(self, redirect_to, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self["Location"] = iri_to_uri(redirect_to)
        self.validate_redirect(redirect_to)

    @property
    def url(self):
        return self["Location"]

    def validate_redirect(self, redirect_to):
        parsed = urlparse(str(redirect_to))
        if not parsed.scheme:
            raise DisallowedRedirect("OAuth2 redirects require a URI scheme.")
