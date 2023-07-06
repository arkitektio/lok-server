from django.http import HttpRequest
from .models import ConfigurationGraph, create_private_client, Client, Manifest, ConfigurationTemplate, Linker, LinkingContext, LinkingClient, LinkingRequest
from oauth2_provider.models import AbstractApplication, get_application_model
from uuid import uuid4
import collections.abc
from .errors import NoConfigurationFound
from django.conf import settings
from urllib.request import urlopen
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
 


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



def create_linking_context(request: HttpRequest, client: Client) -> LinkingContext:


    host_string =  request.get_host().split(":")
    if len(host_string) == 2:
        host = host_string[0]
        port = host_string[1]
    else:
        host = host_string[0]
        port = None




    return LinkingContext(
        request=LinkingRequest(
            host=host,
            port=port,
            is_secure=request.is_secure(),
        ),
        manifest=Manifest(
            identifier=client.release.app.identifier,
            version=client.release.version,
            scopes=client.scopes,
            redirect_uris=client.oauth2_client.redirect_uris.split(" "),
        ), 
        client=LinkingClient(
            client_id=client.client_id,
            client_secret=client.client_secret,
            client_type=client.oauth2_client.client_type,
            authorization_grant_type=client.oauth2_client.authorization_grant_type,
            name=client.oauth2_client.name,
            scopes=client.scopes,
        )
    )


def get_fitting_template_for_context(context: LinkingContext) -> ConfigurationTemplate:

    linkers = Linker.objects.all()

    x = []
    for i in linkers:
        rank = i.rank(context)
        print(rank, i.template.name)
        if rank != -1:
            x.append((i, rank))

    

    x.sort(key=lambda x: x[1], reverse=True)

    if len(x) == 0:
        raise NoConfigurationFound(f"Could not find a fitting configuration for {context}")
    else:
        return x[0][0].template


def render_template(template: ConfigurationTemplate, context: LinkingContext) -> dict:
    return template.render(context)





def claim_client(client: Client, graph: ConfigurationGraph):
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
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "grant_type": client.oauth2_client.authorization_grant_type,
                "name": client.oauth2_client.name,
                "scopes": client.scopes,
            }
        },
    )


def download_logo(url: str) -> File:
    img_tmp = NamedTemporaryFile(delete=True)
    with urlopen(url) as uo:
        assert uo.status == 200
        img_tmp.write(uo.read())
        img_tmp.flush()
    return File(img_tmp)


def download_placeholder(identifier: str, version: str) -> File:
    return download_logo(
        f"https://eu.ui-avatars.com/api/?name={identifier}&background=random"
    )