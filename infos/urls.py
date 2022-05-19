from django.urls import re_path

from . import views


app_name = "infos"


base_urlpatterns = [
    re_path(r"^configure/$", views.ConfigureView.as_view(), name="configure"),
    re_path(r"^retrieve/$", views.RetrieveView.as_view(), name="retrieve"),
    re_path(r"^challenge/$", views.ChallengeView.as_view(), name="challenge"),
    re_path(r"^device/$", views.DeviceView.as_view(), name="device"),
    re_path(r"^claim/$", views.ClaimView.as_view(), name="claim"),
]


urlpatterns = base_urlpatterns
