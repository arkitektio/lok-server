from django import forms
from django.conf import settings
from .models import ConfigurationGraph
from oauth2_provider.models import get_application_model


class ConfigureForm(forms.Form):
    name = forms.CharField(
        required=True,
        help_text="Give this specific app a memorable name",
    )
    redirect_uri = forms.CharField(widget=forms.HiddenInput(), required=False)
    grant = forms.CharField(widget=forms.HiddenInput(), required=True)
    version = forms.CharField(help_text="Declare a version app", required=True, empty_value="latest")
    identifier = forms.CharField(help_text="Declare a unique identifier for this app", required=True)
    state = forms.CharField(required=False, widget=forms.HiddenInput())
    device_code = forms.CharField(required=False, widget=forms.HiddenInput())
    scopes = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        choices=[
            (key, f"{key}: {value}")
            for key, value in settings.OAUTH2_PROVIDER["SCOPES"].items()
        ],
    )
    claim = forms.ModelChoiceField(
        queryset=get_application_model().objects.all(),
        help_text="The App to claim the scopes from",
        widget=forms.HiddenInput(),
        required=False,
    )


class ClaimForm(forms.Form):
    claim = forms.ModelChoiceField(
        queryset=get_application_model().objects.all(),
        help_text="The App to claim the scopes from",
    )


class DeviceForm(forms.Form):
    device_code = forms.CharField(required=True)
