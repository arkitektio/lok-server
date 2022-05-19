import os
from django.http import request
from balder.types import BalderMutation, BalderQuery
from infos import types, models, scalars
import graphene


class DetailMember(BalderQuery):
    """Get information on your Docker Template"""

    class Arguments:
        id = graphene.ID(description="The Whale ID", required=True)

    def resolve(root, info, *args, id=None, template=None):
        return models.Member.objects.get(id=id)

    class Meta:
        type = types.Member
        operation = "member"


class Graphs(BalderQuery):
    class Meta:
        type = types.Graph
        list = True


class CreateElement(BalderMutation):
    class Arguments:
        graph = graphene.ID(
            required=True, description="The Repo of the Docker (Repo on Dockerhub)"
        )
        name = graphene.String(
            description="The Name of this Elemet",
            required=True,
        )
        values = graphene.Argument(
            scalars.Config,
            description="The configuraiton",
            required=True,
        )

    def mutate(root, info, graph, name, values):

        model = models.ConfigurationElement.objects.create(
            graph_id=graph, name=name, values=values
        )

        return model

    class Meta:
        type = types.Element


class CreateGraph(BalderMutation):
    class Arguments:
        name = graphene.String(
            description="The Name of this Elemet",
            required=True,
        )

    def mutate(root, info, name):

        model = models.ConfigurationGraph.objects.create(name=name)

        return model

    class Meta:
        type = types.Graph
