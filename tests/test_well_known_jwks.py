import pytest
from django.test import Client


@pytest.mark.django_db
def test_well_known_jwks_matches_o_jwks():
    """The hub claim's `auth.jwks_url` points at /.well-known/jwks.json — it must resolve."""
    client = Client()
    well_known = client.get("/lok/.well-known/jwks.json")
    canonical = client.get("/lok/o/jwks/")
    assert well_known.status_code == 200
    assert well_known.json() == canonical.json()
    assert well_known.json()["keys"]
