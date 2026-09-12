import strawberry_django
from komment import models, scalars, enums, filters
import strawberry
from typing import Optional
from typing import Annotated, Literal, Union
import datetime
from strawberry.experimental import pydantic
from pydantic import BaseModel, Field
from karakter import types
from karakter.authz import get_user
from django.db.models import Q
from kante.types import Info
import json


class DescendantModel(BaseModel):
    kind: str
    children: list["DescendantUnion"] | None = None


@pydantic.interface(
    DescendantModel,
    description="A descendant of a comment. Descendend are used to render rich text in the frontend.",
)
class Descendant:
    kind: enums.DescendantKind = strawberry.field(
        description="The Kind of a Descendant"
    )
    children: list[Annotated["Descendant", strawberry.lazy(__name__)]] | None = strawberry.field(
        default=None, description="The children of this descendant. Always empty for leafs"
    )

    @strawberry.field(
        description="Unsafe children are not typed and fall back to json. This is a workaround if queries get too complex."
    )
    def unsafe_children(self, info: Info) -> list[scalars.UnsafeChild] | None:
        return json.loads(json.dumps(self.children)) if self.children else None


class LeafDescendantModel(DescendantModel):
    kind: Literal["LEAF"]
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    text: str | None = None
    code: bool | None = None


@pydantic.type(
    LeafDescendantModel,
    description="A leaf of text. This is the most basic descendant and always ends a tree.",
)
class LeafDescendant(Descendant):
    bold: bool | None = strawberry.field(default=None, description="Should we render this text bold?")
    italic: bool | None = strawberry.field(
        default=None,description="Should we render this text italic?"
    )
    underline: bool | None = strawberry.field(
        default=None,description="Should we render this text underlined?"
    )
    text: str | None = strawberry.field(default=None,description="The text of the leaf")
    code: bool | None = strawberry.field(
        default=None,description="Should we render this text as code?"
    )


class MentionDescendantModel(DescendantModel):
    kind: Literal["MENTION"]
    user: str | None = None


@pydantic.type(MentionDescendantModel, description="A mention of a user")
class MentionDescendant(Descendant):
    user: types.User | None = strawberry.field(
        default=None, description="The user that got mentioned"
    )


class ParagraphDescendantModel(DescendantModel):
    kind: Literal["PARAGRAPH"]
    size: str | None = None


@pydantic.type(ParagraphDescendantModel, description="A Paragraph of text")
class ParagraphDescendant(Descendant):
    size: str | None = strawberry.field(default=None, description="The size of the paragraph")


DescendantUnion = Union[
    LeafDescendantModel, MentionDescendantModel, ParagraphDescendantModel
]

DescendantModel.model_rebuild()
LeafDescendantModel.model_rebuild()
MentionDescendantModel.model_rebuild()
ParagraphDescendantModel.model_rebuild()


class Serializer(BaseModel):
    """A simple serializer to convert the descendants to a pydantic model. As union types are not supported yet, we need to do this manually."""

    inside: list[DescendantUnion] = Field(
        default_factory=list,
        description="The descendants to serialize. This is a list of DescendantUnion types.",
    )


@strawberry_django.type(models.Comment, ordering=filters.CommentOrdering)
class Comment:
    id: strawberry.ID
    object: str
    identifier: scalars.Identifier
    children: list["Comment"] = strawberry.field(
        description="The children of this comment"
    )
    parent: Optional["Comment"]
    created_at: datetime.datetime
    mentions: list[types.User]
    resolved_by: types.User | None
    user: types.User

    @strawberry_django.field
    def descendants(self, info: Info) -> list[Descendant]:
        return Serializer(inside=self.descendants).inside if self.descendants else []

    @strawberry.field
    def resolved(self, info: Info) -> bool:
        return self.resolved_by is not None

    @classmethod
    def get_queryset(cls, queryset, info: Info, **kwargs):
        """Comments are visible to their author and to the users they mention.

        Without this the root `comments` list (and `comment(id:)`) returned every
        user's comments across every tenant.
        """
        user = get_user(info)
        return queryset.filter(Q(user=user) | Q(mentions=user)).distinct()
