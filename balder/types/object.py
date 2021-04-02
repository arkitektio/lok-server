from graphene_django import DjangoObjectType










class BalderObject(DjangoObjectType):

    class Meta:
        abstract= True


    def resolve_tags(self, *args, **kwargs):
        if isinstance(self.tags, list):
            return map(str, self.tags)
        else:
            return map(str, self.tags.all())