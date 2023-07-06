from balder.types import BalderObject
from infos import models
import graphene
from lord.types import Application
from infos.enums import FilterMethod

class Graph(BalderObject):
    class Meta:
        model = models.ConfigurationGraph


class Element(BalderObject):
    class Meta:
        model = models.ConfigurationElement


class Configuration(BalderObject):
    class Meta:
        model = models.ConfigurationTemplate

class Linker(BalderObject):

    class Meta:
        model = models.Linker

class Filter(BalderObject):
    method = FilterMethod(required=True)

    class Meta:
        model = models.Filter


class Member(BalderObject):
    class Meta:
        model = models.Member

class App(BalderObject):
    logo = graphene.String(required=False)

    def resolve_logo(self, info):
        return self.logo.url if self.logo else None


    class Meta:
        model = models.App


class Release(BalderObject):
    logo = graphene.String(required=False)

    def resolve_logo(self, info):
        return self.logo.url if self.logo else None
    

    class Meta:
        model = models.Release

class Client(BalderObject):
    scopes = graphene.List(graphene.String, required=True)


    class Meta:
        model = models.Client





class DeviceCode(BalderObject):
    class Meta:
        model = models.DeviceCode
