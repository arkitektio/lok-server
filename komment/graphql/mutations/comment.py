from kante.types import Info
import strawberry
from komment import types, models, inputs, scalars
from django.db.models import Q
from karakter.authz import get_or_denied, get_organization, get_user
import logging
from typing import Dict, Tuple, List, Any

logger = logging.getLogger(__name__)
from django.contrib.auth import get_user_model


def recurse_parse_decendents(
    variables: List[Dict[str, Any]],
) -> Tuple[Dict, List[str]]:
    """Parse Variables

    Recursively traverse variables, applying the apply function to the value if the predicate
    returns True.

    Args:
        variables (Dict): The dictionary to parse.

    Returns:
        Dict: The parsed dictionary.
        Mentions: A list of mentions.

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


@strawberry.input
class CreateCommentInput:
    descendants: list[inputs.DescendantInput]
    identifier: scalars.Identifier
    object: strawberry.ID
    parent: strawberry.ID | None = None
    notify: bool | None = False


def create_comment(info: Info, input: CreateCommentInput) -> types.Comment:
    creator = get_user(info)
    organization = get_organization(info)

    serialized_descendants = strawberry.asdict(input)["descendants"]

    dicted_variables, mentions = recurse_parse_decendents(serialized_descendants)

    # TODO: Check if user is allowed to comment on these types of objects

    # A caller-supplied `parent` must be a comment the caller may actually see.
    # It was written straight to the FK column, which let anyone thread a reply
    # onto *any* comment in the deployment by guessing a (sequential) pk — and
    # then read that comment back through the `parent` hop, which is a forward
    # FK and therefore does not re-apply `Comment.get_queryset`. That was a
    # cross-tenant read of the comment body and its author's username/email.
    parent = None
    if input.parent is not None:
        parent = get_or_denied(
            models.Comment.objects.distinct(),
            Q(user=creator) | Q(mentions=creator),
            id=input.parent,
        )

    exp = models.Comment.objects.create(
        identifier=input.identifier,
        object=input.object,
        user=creator,
        text="",
        descendants=serialized_descendants,
        parent=parent,
    )

    # Mentioned users must be members of the caller's organization: a mention
    # notifies the user and grants them read access to the comment, so it must
    # not reach across tenants. A malformed or foreign id is denied uniformly.
    users = [
        get_or_denied(
            get_user_model().objects.distinct(),
            id=m.get("user"),
            memberships__organization=organization,
        )
        for m in mentions
    ]
    if input.notify:
        for user in users:
            user.notify(
                f"You have been mentioned in a comment by {creator.username}",
                f"Comment on {input.identifier}",
            )

    exp.mentions.set(users)
    exp.save()

    return exp


@strawberry.input
class ReplyToCommentInput:
    descendants: list[inputs.DescendantInput]
    parent: strawberry.ID | None
    notify: bool | None


def reply_to(info: Info, input: ReplyToCommentInput) -> types.Comment:
    raise NotImplementedError("TODO: Implement")


@strawberry.input
class ResolveCommentInput:
    id: strawberry.ID
    notify: bool | None


def resolve_comment(info: Info, input: ResolveCommentInput) -> types.Comment:
    raise NotImplementedError("TODO: Implement")
