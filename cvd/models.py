import secrets
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from karakter.models import User


def _reference() -> str:
    """A short, unguessable handle the reporter can quote (``VR-7K3Q-9XW2``)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"VR-{raw[:4]}-{raw[4:]}"


class VulnerabilityReport(models.Model):
    """A report submitted through the public, anonymous web form (BSI TR-03183-3 4.3.3).

    Deliberately holds nothing about the submitter beyond what they typed: no IP address,
    no user agent, no session or account. The form must allow anonymous reports (4.3.3c),
    and a stored address would quietly undo that.
    """

    class Kind(models.TextChoices):
        PRODUCT = "product", "Product (software we ship)"
        INFRASTRUCTURE = "infrastructure", "Infrastructure (a service we operate)"
        UNSURE = "unsure", "Not sure"

    class Status(models.TextChoices):
        NEW = "new", "New"
        TRIAGE = "triage", "In triage"
        CONFIRMED = "confirmed", "Confirmed"
        FIXED = "fixed", "Fixed"
        DISCLOSED = "disclosed", "Publicly disclosed"
        REJECTED = "rejected", "Rejected (not a vulnerability)"
        CLOSED = "closed", "Closed"

    reference = models.CharField(
        max_length=16, unique=True, default=_reference, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.UNSURE)
    title = models.CharField(max_length=200)
    affected = models.CharField(
        max_length=500,
        help_text="Product, version, URL or component the reporter named.",
    )
    description = models.TextField(
        help_text="What the vulnerability is and its impact."
    )
    reproduction = models.TextField(
        blank=True, help_text="Steps to reproduce / proof of concept."
    )
    language = models.CharField(max_length=8, default="en")

    contact = models.CharField(
        max_length=300,
        blank=True,
        help_text="Email address, phone number or other contact the reporter chose. Empty: anonymous.",
    )
    credit_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Name/alias for the acknowledgements page.",
    )
    credit_reference = models.CharField(
        max_length=300, blank=True, help_text="URL the reporter wants linked."
    )
    wants_credit = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    internal_notes = models.TextField(blank=True)

    # Closing takes two people (TR-03183-3 4.4.7g: a report cannot be closed by a single
    # analyst). The admin binds both to the logged-in user (cvd/admin.py): one analyst
    # proposes a closing status, and only a save by a *different* analyst applies it.
    close_proposed_status = models.CharField(
        max_length=20, choices=Status.choices, blank=True
    )
    close_proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    close_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    CLOSING = (Status.REJECTED, Status.CLOSED)

    def request_status(self, status: str, user: "User") -> str:
        """Move to ``status`` on behalf of ``user``. Returns what happened, for the admin message.

        Non-closing statuses apply directly. A closing status from one analyst is only a
        proposal; it applies when a different analyst asks for the same closing status.
        """
        if status == self.status:
            # Editing notes leaves a pending proposal alone.
            return "unchanged"
        if status not in self.CLOSING:
            self.status = status
            (
                self.close_proposed_status,
                self.close_proposed_by,
                self.close_confirmed_by,
            ) = "", None, None
            return "applied"
        if (
            self.close_proposed_status == status
            and self.close_proposed_by_id is not None
            and self.close_proposed_by_id != user.pk
        ):
            self.status = status
            self.close_confirmed_by = user
            return "confirmed"
        self.close_proposed_status = status
        self.close_proposed_by = user
        self.close_confirmed_by = None
        return "proposed"

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.reference}: {self.title}"

    @property
    def is_anonymous(self) -> bool:
        return not self.contact.strip()
