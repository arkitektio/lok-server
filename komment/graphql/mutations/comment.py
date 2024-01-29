from typing import Dict, Tuple
from django.contrib.auth import get_user_model
from graphene.types.generic import GenericScalar
from balder.types import BalderMutation
import graphene
from komment import models, types
import logging
import datetime


UserModel = get_user_model()


class DescendendKind(graphene.Enum):
    """The kind of the comment"""

    MENTION = "MENTION"
    PARAGRAPH = "PARAGRAPH"
    LEAF = "LEAF"


class DescendendInput(graphene.InputObjectType):
    children = graphene.List(lambda: DescendendInput, required=False)
    kind = DescendendKind(
        description="The type of the descendent", required=False, default_value="LEAF"
    )
    user = graphene.String(description="The user that is mentioned", required=False)
    bold = graphene.Boolean(description="Is this a bold leaf?", required=False)
    italic = graphene.Boolean(description="Is this a italic leaf?", required=False)
    code = graphene.Boolean(description="Is this a code leaf?", required=False)
    text = graphene.String(description="The text of the leaf", required=False)


def recurse_parse_decendents(
    variables: Dict,
) -> Tuple[Dict, Dict]:
    """Parse Variables

    Recursively traverse variables, applying the apply function to the value if the predicate
    returns True.

    Args:
        variables (Dict): The dictionary to parse.
        predicate (Callable[[str, Any], bool]):The path this is in
        apply (Callable[[Any], Any]): _description_

    Returns:
        Dict: _description_
    """

    mentions = []

    def recurse_extract(obj, path: str = None):
        """
        recursively traverse obj, doing a deepcopy, but
        replacing any file-like objects with nulls and
        shunting the originals off to the side.
        """

        if isinstance(obj, list):
            nulled_obj = []
            for key, value in enumerate(obj):
                value = recurse_extract(
                    value,
                    f"{path}.{key}" if path else key,
                )
                nulled_obj.append(value)
            return nulled_obj
        elif isinstance(obj, dict):
            nulled_obj = {}
            for key, value in obj.items():
                if key == "kind" and value == "MENTION":
                    mentions.append(obj)
                value = recurse_extract(value, f"{path}.{key}" if path else key)
                nulled_obj[key] = value
            return nulled_obj
        else:
            return obj

    dicted_variables = recurse_extract(variables)

    return dicted_variables, mentions


class CreateComment(BalderMutation):
    """Create an Comment

    This mutation creates a comment. It takes a commentable_id and a commentable_type.
    If this is the first comment on the commentable, it will create a new comment thread.
    If there is already a comment thread, it will add the comment to the thread (by setting
    it's parent to the last parent comment in the thread).

    CreateComment takes a list of Descendents, which are the comment tree. The Descendents
    are a recursive structure, where each Descendent can have a list of Descendents as children.
    The Descendents are either a Leaf, which is a text node, or a MentionDescendent, which is a
    reference to another user on the platform.

    Please convert your comment tree to a list of Descendents before sending it to the server.
    TODO: Add a converter from a comment tree to a list of Descendents.


    (only signed in users)"""

    class Arguments:
        identifier = graphene.String(
            description="The commentable identifier", required=True
        )
        object = graphene.ID(
            required=True, description="The Representationss this sROI belongs to"
        )
        descendents = graphene.List(
            DescendendInput, required=True, description="The comment tree"
        )
        parent = graphene.ID(description="The parent comment", required=False)
        notify_mentions = graphene.Boolean(
            description="Should we notify the mentioned users?", required=False
        )

    def mutate(
        root, info, identifier, object, descendents, parent=None, notify_mentions=False
    ):
        creator = info.context.user

        dicted_variables, mentions = recurse_parse_decendents(descendents)

        users = [UserModel.objects.get(id=m["user"]) for m in mentions]
        if notify_mentions:
            for user in users:
                user.notify(
                    f"You have been mentioned in a comment by {creator.name}",
                    f"Comment on {identifier}",
                )

        # TODO: Check if user is allowed to comment on these types of objects

        exp = models.Comment.objects.create(
            identifier=identifier,
            object=object,
            user=creator,
            text="",
            descendents=descendents,
            parent_id=parent,
        )
        print(users)
        exp.mentions.set(users)
        exp.save()

        return exp

    class Meta:
        type = types.Comment


class ReplyTo(BalderMutation):
    """Reply to an Comment

    This mutation creates a comment. It takes a commentable_id and a commentable_type.
    If this is the first comment on the commentable, it will create a new comment thread.
    If there is already a comment thread, it will add the comment to the thread (by setting
    it's parent to the last parent comment in the thread).

    CreateComment takes a list of Descendents, which are the comment tree. The Descendents
    are a recursive structure, where each Descendent can have a list of Descendents as children.
    The Descendents are either a Leaf, which is a text node, or a MentionDescendent, which is a
    reference to another user on the platform.

    Please convert your comment tree to a list of Descendents before sending it to the server.
    TODO: Add a converter from a comment tree to a list of Descendents.


    (only signed in users)"""

    class Arguments:
        descendents = graphene.List(
            DescendendInput, required=True, description="The comment tree"
        )
        parent = graphene.ID(description="The parent comment", required=True)

    def mutate(root, info, descendents, parent):
        creator = info.context.user
        parent = models.Comment.objects.get(id=parent)

        dicted_variables, mentions = recurse_parse_decendents(descendents)

        users = [UserModel.objects.get(id=m["user"]) for m in mentions]

        exp = models.Comment.objects.create(
            object=parent.object,
            identifier=parent.identifier,
            user=creator,
            text="",
            descendents=descendents,
            parent=parent,
        )
        exp.mentions.set(users)
        exp.save()

        return exp

    class Meta:
        type = types.Comment


class ResolveComment(BalderMutation):
    """Create an Comment

    This mutation resolves a comment. By resolving a comment, it will be marked as resolved,
    and the user that resolved it will be set as the resolver.

    (only signed in users)"""

    class Arguments:
        id = graphene.ID(required=True, description="The comments id")
        imitate = graphene.ID(
            description="Should we imitate the resolving by another user (requires imitate permission)",
            required=False,
        )

    def mutate(root, info, id, imitate=None):
        resolver = info.context.user
        UserModel = get_user_model()

        if imitate:
            resolver = UserModel.objects.get(id=imitate)
            # TODO: check imitation permission

        exp = models.Comment.objects.get(id=id)
        # TODO: Check persmission of user to resolve comment
        assert exp.resolved is None, "Comment is already resolved"

        exp.resolved = datetime.datetime.now()
        exp.resolved_by = resolver
        exp.save()

        return exp

    class Meta:
        type = types.Comment
