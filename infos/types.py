from balder.types import BalderObject
from infos import models


class Graph(BalderObject):
    class Meta:
        model = models.ConfigurationGraph


class Element(BalderObject):
    class Meta:
        model = models.ConfigurationElement


class Member(BalderObject):
    class Meta:
        model = models.Member
