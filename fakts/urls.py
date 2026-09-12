from django.urls import re_path

from . import views


app_name = "fakts"


def index(request):
    # Render that in the index template
    raise NotImplementedError("This view is not implemented yet.")


# Basic url patterns for fakts
# App-facing API endpoints only (user-facing views are handled by React frontend)
#
# The client flow's endpoints all live under the OAuth handle now: the app
# authorization (device authorization + dynamic client registration) endpoint
# is /o/app-authorization/ (see authapp/urls.py, view in fakts/views.py), and
# the canonical fakts grant runs on the token endpoint (/o/token/) — apps poll
# it with grant_type=urn:ietf:params:oauth:grant-type:device_code, headless
# clients exchange redeem tokens via urn:fakts:grant-type:redeem, and both
# receive tokens + instances in one response.
base_urlpatterns = [
    re_path(r"^$", index, name="index"),
    re_path(r"^report/$", views.ReportView.as_view(), name="report"),
    re_path(r"^meshstart/$", views.MeshStartChallengeView.as_view(), name="meshstart"),
    re_path(r"^meshchallenge/$", views.MeshChallengeView.as_view(), name="meshchallenge"),
    re_path(r"^claimhub/$", views.ClaimHubView.as_view(), name="hubclaim"),
]


urlpatterns = base_urlpatterns
