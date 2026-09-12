from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from fakts.services.hubs import PartnerPreAuthorizationError, create_hub_from_partner, run_partner_pre_authorize_hook


class PartnerPreAuthorizeHookTests(SimpleTestCase):
	def test_run_partner_pre_authorize_hook_accepts_ok_json(self):
		partner = SimpleNamespace(
			pk="1",
			identifier="demo",
			name="Demo Partner",
			pre_authorize_hook="https://partner.example/hook",
			pre_authorize_token="secret-token",
		)
		organization = SimpleNamespace(pk="2", slug="demo-org", name="Demo Org")
		hub = SimpleNamespace(pk="3", identifier="demo-comp", name="Demo Hub", token="comp-token")

		with patch("fakts.services.hubs.requests.post") as post:
			post.return_value.json.return_value = {"ok": True}
			post.return_value.raise_for_status.return_value = None

			run_partner_pre_authorize_hook(
				partner=partner,
				organization=organization,
				hub=hub,
				hub_config={"identifier": "demo-config"},
				license_signature="Jane Doe",
			)

		post.assert_called_once()
		_, kwargs = post.call_args
		assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
		assert kwargs["json"]["license_signature"] == "Jane Doe"

	def test_create_hub_from_partner_deletes_hub_when_hook_rejects(self):
		hub = SimpleNamespace(identifier="demo-comp", delete=MagicMock())
		partner = SimpleNamespace(
			identifier="demo",
			preconfigured_hub_as_model=object(),
			preconfigured_hub={"identifier": "demo-config"},
		)
		organization = SimpleNamespace(slug="demo-org")

		with patch("fakts.services.hubs.create_hub_from_manifest", return_value=hub), patch(
			"fakts.services.hubs.run_partner_pre_authorize_hook",
			side_effect=PartnerPreAuthorizationError("Denied by partner"),
		):
			with self.assertRaisesMessage(PartnerPreAuthorizationError, "Denied by partner"):
				create_hub_from_partner(
					partner=partner,
					organization=organization,
					license_signature="Jane Doe",
				)

		hub.delete.assert_called_once()
