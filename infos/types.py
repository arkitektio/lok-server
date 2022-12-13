from balder.types import BalderObject
from infos import models
import graphene

class Graph(BalderObject):
    class Meta:
        model = models.ConfigurationGraph


class Element(BalderObject):
    class Meta:
        model = models.ConfigurationElement


class Member(BalderObject):
    class Meta:
        model = models.Member


class FaktApplication(BalderObject):
    scopes = graphene.List(graphene.String, required=True)


    class Meta:
        model = models.FaktApplication


class App(BalderObject):
    logo = graphene.String(required=False)

    def resolve_logo(self, info):
        return self.logo.url if self.logo else None


    class Meta:
        model = models.App


class DeviceCode(BalderObject):
    class Meta:
        model = models.DeviceCode
