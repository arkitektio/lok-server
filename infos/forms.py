from django import forms
from django.conf import settings
from . import models
from oauth2_provider.models import get_application_model
import re
from jinja2 import Environment, TemplateSyntaxError, TemplateError
import yaml
from .validators import jinja2_yaml_template_validator


class ConfigureForm(forms.Form):
    device_code = forms.CharField(required=False, widget=forms.HiddenInput())
    scopes = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        choices=[
            (key, f"{key}: {value}")
            for key, value in settings.OAUTH2_PROVIDER["SCOPES"].items()
        ],
    )

class ClaimForm(forms.Form):
    claim = forms.ModelChoiceField(
        queryset=get_application_model().objects.all(),
        help_text="The App to claim the scopes from",
    )


class DeviceForm(forms.Form):
    device_code = forms.CharField(required=True)

def validate_host(host):
    try: 
        re.compile(host)
    except re.error:
        raise forms.ValidationError("This is not a valid regex")

    if models.ConfigurationGraph.objects.filter(host=host).exists():
        raise forms.ValidationError("This host is already in use")
        

class ConfigurationGraphForm(forms.Form):
    name = forms.CharField(max_length=400)
    version = forms.CharField(max_length=600)
    host = forms.CharField(
        max_length=500, help_text="Is this appearing on a selection of hosts?",
        validators=[validate_host]
    )


    def save(self, commit=True):
        return models.ConfigurationGraph.objects.create(**self.cleaned_data)


class FilterForm(forms.ModelForm):
    
    class Meta:
        model = models.Filter
        fields = "__all__"

    def save(self, commit=True):
        return models.Filter.objects.create(**self.cleaned_data)



class LinkerForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=models.ConfigurationTemplate.objects.all(), 
    )
    priority = forms.IntegerField()

    class Meta:
        model = models.Linker
        fields = "__all__"

    def save(self, commit=True):
        return models.Linker.objects.create(**self.cleaned_data)


class ConfigurationTemplateForm(forms.Form):
    name = forms.CharField(max_length=400)
    body = forms.CharField(validators=[jinja2_yaml_template_validator])

    def save(self, commit=True):
        return models.ConfigurationTemplate.objects.create(**self.cleaned_data)