from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from fakts.models import Client
from karakter.models import Membership, Organization


User = get_user_model()


class OrganizationClientCleanupTests(TestCase):
    def test_deleting_organization_deletes_membership_bound_clients(self):
        owner = User.objects.create_user(username="owner", password="secret")
        organization = Organization.objects.create(
            owner=owner,
            slug="demo-org",
            name="Demo Org",
        )
        membership = Membership.objects.get(user=owner, organization=organization)
        client = Client.objects.create(
            membership=membership,
            client_id="client-id",
            client_secret="client-secret",
        )

        organization.delete()

        self.assertFalse(Client.objects.filter(pk=client.pk).exists())

    def test_deleting_organization_deletes_org_bound_clients(self):
        owner = User.objects.create_user(username="owner2", password="secret")
        organization = Organization.objects.create(
            owner=owner,
            slug="demo-org-2",
            name="Demo Org 2",
        )
        client = Client.objects.create(
            client_id="client-id-2",
            client_secret="client-secret-2",
            organization=organization,
        )

        organization.delete()

        self.assertFalse(Client.objects.filter(pk=client.pk).exists())


class TokenEndpointHardeningTests(TestCase):
    def test_client_credentials_is_not_a_registered_grant(self):
        client = Client.objects.create(
            client_id="orphan-client-id",
            client_secret="orphan-client-secret",
            token_endpoint_auth_method="client_secret_post",
            grant_types="client_credentials",
        )

        response = self.client.post(
            reverse("token"),
            {
                "grant_type": "client_credentials",
                "client_id": client.client_id,
                "client_secret": client.client_secret,
            },
            secure=True,
        )

        # client_credentials is no longer registered at all — nothing in the
        # deployment uses it, so the grant surface was narrowed.
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unsupported_grant_type")
