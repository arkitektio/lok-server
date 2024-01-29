from balder.types.query import BalderQuery
import graphene
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from komment import types, models
from balder.registry import register_type
import graphene


class CommentsFor(BalderQuery):
    """Comments for a specific object

    This query returns all comments for a specific object. The object is
    specified by the `model` and `id` arguments. The `model` argument is
    a string that is the name of the model. The `id` argument is the id of
    the object.

    You can only query for comments for objects that you have access to.

    """

    class Arguments:
        deep = graphene.Boolean(required=False)
        identifier = graphene.String(
            description="The identifier of the object", required=True
        )
        object = graphene.ID(required=True)

    def resolve(self, info, identifier, object, deep=False):
        f = models.Comment.objects.filter(identifier=identifier, object=object)

        if not deep:
            f = f.filter(parent=None)

        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "commentsfor"


class MyMentions(BalderQuery):
    def resolve(self, info):
        f = info.context.user.mentioned_in
        return f.order_by("-created_at").all()

    class Meta:
        type = types.Comment
        list = True
        operation = "mymentions"


class Comment(BalderQuery):
    class Arguments:
        id = graphene.ID(required=True)

    def resolve(self, info, id):
        return models.Comment.objects.get(id=id)

    class Meta:
        type = types.Comment
        list = False
        operation = "comment"
