from django.core.exceptions import ValidationError
from jinja2 import Template, TemplateSyntaxError, TemplateError, StrictUndefined
import yaml
from .models import LinkingContext, LinkingClient, Manifest, LinkingRequest

def is_valid_jinja2_template(template_string, render_context=None):
    try:
        template =Template(template_string, undefined=StrictUndefined)
        try:
            rendered_template = template.render(render_context )
            yaml.safe_load(rendered_template)
        except (TemplateError, yaml.YAMLError) as e:
            raise ValidationError(f"Rendering error: {e}")
    except TemplateSyntaxError as e:
        raise ValidationError(f"Template syntax error: {e}")
    



def jinja2_yaml_template_validator(value, render_context=None):

    fake_context = LinkingContext(
        request=LinkingRequest(
            host="example.com",
            port="443",
            is_secure=True,
        ),
        manifest=Manifest(
            identifier="com.example.app",
            version="1.0",
            scopes=["scope1", "scope2"],
            redirect_uris=["https://example.com"],
        ),
        client=LinkingClient(
            client_id="@client_id",
            client_secret="@client_secret",
            client_type="@client_type",
            authorization_grant_type="authorization_grant_type",
            name="@name",
            scopes=["scope1", "scope2"],
        ),
    )
    try:
        is_valid_jinja2_template(value, fake_context.dict())
    except ValidationError as e:
        raise ValidationError(e)