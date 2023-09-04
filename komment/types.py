from django.contrib.auth import get_user_model
from balder.types.object import BalderObject
import graphene
from komment import models
from django.contrib.auth.models import Group as GroupModel
from balder.registry import register_type
from graphene.types.generic import GenericScalar
from lord import types

descendent_map = lambda: {
    "MENTION": MentionDescendent,
    "PARAGRAPH": ParagraphDescendent,
    "LEAF": Leaf,
}


@register_type
class CommentNode(graphene.Interface):
    """A node in the comment tree"""

    children = graphene.List(lambda: Descendent)
    untyped_children = GenericScalar()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get("typename", None)

    def resolve_untyped_children(root, info):
        return root.get("children", [])


@register_type
class Descendent(graphene.Interface):
    """A descendent of a node in the comment tree"""

    typename = graphene.String()

    @classmethod
    def resolve_type(cls, instance, info):
        typemap = descendent_map()
        return typemap.get(instance.get("kind"), None)


@register_type
class Leaf(graphene.ObjectType):
    """A leaf in the comment tree. Representations some sort of text"""

    bold = graphene.Boolean(description="Is this a bold leaf?")
    italic = graphene.Boolean(description="Is this a italic leaf?")
    code = graphene.Boolean(description="Is this a code leaf?")
    text = graphene.String(description="The text of the leaf")

    class Meta:
        interfaces = (Descendent,)


@register_type
class MentionDescendent(graphene.ObjectType):
    """A mention in the comment tree. This  is a reference to another user on the platform"""

    user = graphene.Field(
        types.User, description="The user that is mentioned", required=True
    )

    class Meta:
        interfaces = (CommentNode, Descendent)

    def resolve_user(root, info):
        return get_user_model().objects.get(id=root.get("user"))


@register_type
class ParagraphDescendent(graphene.ObjectType):
    """A paragraph in the comment tree. This paragraph contains other nodes (list nodes)"""

    size = graphene.String(description="The size of the paragraph", required=False)

    class Meta:
        interfaces = (CommentNode, Descendent)


class Comment(BalderObject):
    """A comment

    A comment is a user generated comment on a commentable object. A comment can be a reply to another comment or a top level comment.
    Comments can be nested to any depth. A comment can be edited and deleted by the user that created it.
    """

    descendents = graphene.List(
        Descendent,
        description="The descendents of the comment (this referes to the Comment Tree)",
    )
    children = graphene.List(
        lambda: Comment,
        limit=graphene.Int(description="How many children to return"),
        offset=graphene.Int(description="The offset for the children"),
        description="Comments that are replies to this comment",
    )
    identifier = graphene.String(
        description="The identifier of the object that this comment is on"
    )
    object = graphene.ID(required=True, description="The Object this comment is on")

    def resolve_children(root, info, *args, offset=0, limit=20):
        return root.children.order_by("-created_at")[offset : offset + limit]

    def resolve_content_type(root, info):
        ct = root.content_type
        return f"{ct.app_label}_{ct.model}".replace(" ", "_").upper()

    class Meta:
        model = models.Comment
