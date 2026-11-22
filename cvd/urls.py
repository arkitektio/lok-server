from django.urls import re_path

from . import views

app_name = "cvd"

# Trailing slashes are optional: the gateway publishes these at /security…, and
# APPEND_SLASH would redirect to lok's internal /lok/security/ path.
urlpatterns = [
    re_path(r"^$", views.ReportPage.as_view(), name="report"),
    re_path(r"^policy/?$", views.PolicyPage.as_view(), name="policy"),
    re_path(
        r"^acknowledgements/?$",
        views.AcknowledgementsPage.as_view(),
        name="acknowledgements",
    ),
    re_path(r"^thanks/?$", views.ThanksPage.as_view(), name="thanks"),
]
