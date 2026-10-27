from pydantic import BaseModel, Field
from typing import Optional


class MembershipConfig(BaseModel):
    organization: str
    is_active: bool = True
    user: str
    roles: list[str] = []


class UserConfig(BaseModel):
    """A Config Item to serialize yaml users"""

    username: str
    password: str
    email: Optional[str] = None
    is_superuser: bool = False
    is_staff: bool = False


class OrganizationConfig(BaseModel):
    id: int = Field(gt=0, description="The organization's id: what every token's ``org`` claim and every service names it by. Fixed here so other services' configuration can name it too.")
    name: str
    owner: str
    description: Optional[str] = None
    identifier: Optional[str] = Field(default=None, description="Lok's own handle for it (its slug), used in lok's pages and invites and by this configuration's memberships and mandate keys; it never leaves lok.")
    parent: Optional[str] = None
    auto_configure: bool = True


class Members(BaseModel):
    memberships: list[MembershipConfig] = []
