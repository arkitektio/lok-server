from django.urls import path

from .views import accept_invite, change_org, leave_org, organization_detail

urlpatterns = [
    path("organization/<slug:slug>/", organization_detail, name="organization_detail"),
    path("organization/<slug:slug>/leave/", leave_org, name="leave_org"),
    path("changeorg/", change_org, name="karakter_changeorg"),
    path("invite/<uuid:token>/", accept_invite, name="accept_invite"),
]
