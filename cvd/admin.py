from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest

from .models import VulnerabilityReport


@admin.register(VulnerabilityReport)
class VulnerabilityReportAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "title",
        "kind",
        "status",
        "close_proposed_status",
        "anonymous",
        "created_at",
    )
    list_filter = ("status", "kind", "wants_credit")
    search_fields = ("reference", "title", "affected", "description")
    # Who proposed and confirmed a close is never typed in: it is whoever saved (4.4.7g).
    readonly_fields = (
        "reference",
        "created_at",
        "close_proposed_status",
        "close_proposed_by",
        "close_confirmed_by",
    )
    fieldsets = (
        (None, {"fields": ("reference", "created_at", "status", "kind", "language")}),
        ("Report", {"fields": ("title", "affected", "description", "reproduction")}),
        (
            "Reporter",
            {"fields": ("contact", "wants_credit", "credit_name", "credit_reference")},
        ),
        (
            "Handling",
            {
                "fields": (
                    "internal_notes",
                    "close_proposed_status",
                    "close_proposed_by",
                    "close_confirmed_by",
                ),
                "description": "Closing or rejecting needs two analysts: the first save only proposes it, "
                "and a different analyst choosing the same status confirms it.",
            },
        ),
    )

    @admin.display(boolean=True)
    def anonymous(self, obj: VulnerabilityReport) -> bool:
        return obj.is_anonymous

    def save_model(
        self,
        request: HttpRequest,
        obj: VulnerabilityReport,
        form: forms.ModelForm,
        change: bool,
    ) -> None:
        wanted = obj.status
        if change:
            obj.status = VulnerabilityReport.objects.values_list(
                "status", flat=True
            ).get(pk=obj.pk)
        else:
            obj.status = VulnerabilityReport.Status.NEW
        outcome = obj.request_status(wanted, request.user)
        if outcome == "proposed":
            self.message_user(
                request,
                f"Closing as '{obj.get_close_proposed_status_display()}' is proposed. A second analyst must confirm it by choosing the same status.",
                messages.WARNING,
            )
        elif outcome == "confirmed":
            self.message_user(
                request, "Close confirmed by a second analyst.", messages.SUCCESS
            )
        super().save_model(request, obj, form, change)
