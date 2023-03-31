from django.http import HttpRequest
from .models import ConfigurationGraph, create_private_fakt
from oauth2_provider.models import AbstractApplication, get_application_model
from uuid import uuid4
import collections.abc
from .errors import NoConfigurationFound
from django.conf import settings


def update_nested(d, u):
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update_nested(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def create_api_token():
    return str(uuid4())


def get_fitting_graph(request: HttpRequest) -> ConfigurationGraph:
    params = {"host": request.get_host()}

    graph = ConfigurationGraph.objects.filter(**params).first()
    if graph is None:
        raise NoConfigurationFound(f"Could Not find this configuration {params}")
    return graph


def configure_new_app(
    user,
    name: str,
    scopes: str,
    version: str,
    identifier: str,
    graph: ConfigurationGraph,
):
    app = create_private_fakt(identifier, version, user, user, scopes)

    client_app = app.application

    config = {}

    for m in graph.elements.all():
        config[m.name] = m.values

    return update_nested(
        config,
        {
            "lok": {
                "client_id": client_app.client_id,
                "client_secret": app.client_secret,
                "grant_type": client_app.authorization_grant_type,
                "scopes": app.scopes,
                "name": client_app.name,
                "version": app.version,
                "identifier": app.identifier,
            }
        },
    )


def configure_new_public_app(
    user,
    name: str,
    scopes: str,
    redirect_uri: str,
    version: str,
    identifier: str,
    graph: ConfigurationGraph,
):
    client_secret = str(uuid4())

    new_app = get_application_model().objects.create(
        name=name,
        user=user,
        client_secret=client_secret,
        redirect_uris=redirect_uri,
        client_type="public",
        identifier=identifier,
        version=version,
        authorization_grant_type="authorization-code",
        algorithm=AbstractApplication.RS256_ALGORITHM,
    )

    config = {}

    for m in graph.elements.all():
        config[m.name] = m.values

    return update_nested(
        config,
        {
            "lok": {
                "grant_type": new_app.authorization_grant_type,
                "client_id": new_app.client_id,
                "client_secret": client_secret,
                "scopes": scopes,
                "name": new_app.name,
            }
        },
    )


def claim_public_app(app, scopes, graph: ConfigurationGraph):
    config = {}

    for m in graph.elements.all():
        config[m.name] = m.values

    return update_nested(
        config,
        {
            "lok": {
                "client_id": app.client_id,
                "client_secret": app.client_secret,
                "grant_type": app.authorization_grant_type,
                "name": app.name,
                "scopes": scopes,
            }
        },
    )


def claim_app(app, client_secret, scopes, graph: ConfigurationGraph):
    config = {
        "self": {
            "name": settings.FAKT_NAME,
        }
    }

    for m in graph.elements.all():
        config[m.name] = m.values

    return update_nested(
        config,
        {
            "lok": {
                "client_id": app.client_id,
                "client_secret": client_secret,
                "grant_type": app.authorization_grant_type,
                "name": app.name,
                "scopes": scopes,
            }
        },
    )
