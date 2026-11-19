"""The public vulnerability disclosure pages (BSI TR-03183-3 4.3.3, 4.4, 4.5).

Server-rendered on purpose: 4.5.1d requires the report page, with all its information,
to work without JavaScript, and the form must accept anonymous reports (4.3.3c). The
form is a plain ``<form method="post">``. It has no CAPTCHA, because third-party script
breaks both requirements. A honeypot field, size caps and a per-IP cache window (TTL
only, nothing stored with the report) keep bulk spam out.
"""

from __future__ import annotations

import logging
import re

from django import forms
from django.conf import settings
from django.core.mail import send_mail
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from authapp.throttle import is_throttled

from .models import VulnerabilityReport
from .securitytxt import expires_value

logger = logging.getLogger(__name__)

#: Reports per minute per client address. A person files one; a script files hundreds.
#: Generous on purpose: unless `django.trusted_proxy_depth` is set, every request behind
#: a gateway shares the gateway's address, so this is effectively a site-wide limit and a
#: tight one would let a single script lock every reporter out (TR 4.3.3f: high availability).
REPORT_LIMIT_PER_MINUTE = 30
_REFERENCE = re.compile(r"^VR-[A-Z0-9]{4}-[A-Z0-9]{4}$")


def _security():
    sec = getattr(settings, "SECURITY_DISCLOSURE", None)
    if sec is None:
        raise Http404("This deployment publishes no vulnerability disclosure contact.")
    return sec


def _grouped(fingerprint: str) -> str:
    return " ".join(fingerprint[i : i + 4] for i in range(0, len(fingerprint), 4))


def _context(sec, **extra):
    base = sec.base_path
    return {
        "sec": sec,
        "base": base,
        "psirt_fpr": _grouped(sec.psirt.key.fingerprint),
        "csirt_fpr": _grouped(sec.csirt.key.fingerprint),
        "signing_fpr": _grouped(sec.signing_key.fingerprint),
        "expires_rfc3339": expires_value(sec.expires),
        "languages": ", ".join(sec.preferred_languages),
        **extra,
    }


class ReportForm(forms.Form):
    kind = forms.ChoiceField(choices=VulnerabilityReport.Kind.choices, initial=VulnerabilityReport.Kind.UNSURE)
    title = forms.CharField(max_length=200)
    affected = forms.CharField(max_length=500)
    description = forms.CharField(max_length=20000, widget=forms.Textarea)
    reproduction = forms.CharField(max_length=20000, required=False, widget=forms.Textarea)
    # Free text on purpose: an email address OR a phone number OR anything else the
    # reporter wants to be reached by (TR-03183-3 4.4.7e). Never validated as email,
    # and never written to automatically: lok sends nothing to it.
    contact = forms.CharField(max_length=300, required=False)
    wants_credit = forms.BooleanField(required=False)
    credit_name = forms.CharField(max_length=120, required=False)
    credit_reference = forms.CharField(max_length=300, required=False)
    language = forms.ChoiceField(choices=(("en", "English"), ("de", "Deutsch")), initial="en")
    # Honeypot: hidden from people by CSS, filled in by naive bots.
    website = forms.CharField(required=False)


class ReportPage(View):
    """The central page for incoming vulnerability reports (4.5), with the form (4.3.3)."""

    def get(self, request):
        sec = _security()
        return render(request, "cvd/report.html", _context(sec, form=ReportForm()))

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        # csrf_exempt: anonymous, sessionless, and a successful forgery only files a report.
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        sec = _security()
        if is_throttled(request, "cvd-report", REPORT_LIMIT_PER_MINUTE):
            return render(request, "cvd/report.html", _context(sec, form=ReportForm(request.POST), throttled=True), status=429)

        form = ReportForm(request.POST)
        if not form.is_valid():
            return render(request, "cvd/report.html", _context(sec, form=form), status=400)

        data = form.cleaned_data
        if data["website"]:
            # A bot. Answer like a success so it learns nothing.
            return HttpResponseRedirect(f"{sec.base_path}/thanks")

        report = VulnerabilityReport.objects.create(
            kind=data["kind"],
            title=data["title"],
            affected=data["affected"],
            description=data["description"],
            reproduction=data["reproduction"],
            contact=data["contact"].strip(),
            wants_credit=data["wants_credit"],
            credit_name=data["credit_name"] if data["wants_credit"] else "",
            credit_reference=data["credit_reference"] if data["wants_credit"] else "",
            language=data["language"],
        )
        _notify(request, sec, report)
        response = HttpResponseRedirect(f"{sec.base_path}/thanks?ref={report.reference}")
        response.status_code = 303
        return response


def _notify(request, sec, report: VulnerabilityReport) -> None:
    """Tell the PSIRT mailbox a report arrived. Only the reference and where to read it:
    the report itself stays in lok and never travels as plaintext email."""
    if getattr(settings, "EMAIL_HOST", None) in (None, "", "localhost"):
        logger.warning("Vulnerability report %s filed, but lok has no outbound email to notify %s", report.reference, sec.psirt.email)
        return
    admin_url = request.build_absolute_uri(reverse("admin:cvd_vulnerabilityreport_change", args=[report.pk]))
    try:
        send_mail(
            subject=f"[{sec.vendor}] New vulnerability report {report.reference}",
            message=(
                f"A vulnerability report was submitted through {sec.page_url}.\n\n"
                f"Reference: {report.reference}\n"
                f"Kind: {report.get_kind_display()}\n"
                f"Anonymous: {'yes' if report.is_anonymous else 'no'}\n\n"
                f"Read and triage it here (staff login required):\n{admin_url}\n\n"
                "Policy: a first, human response within five working days, detailed feedback within ten.\n"
            ),
            from_email=None,
            recipient_list=[sec.psirt.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not notify %s about vulnerability report %s", sec.psirt.email, report.reference)


class ThanksPage(View):
    def get(self, request):
        sec = _security()
        ref = request.GET.get("ref", "")
        return render(request, "cvd/thanks.html", _context(sec, reference=ref if _REFERENCE.match(ref) else None))


class PolicyPage(View):
    def get(self, request):
        return render(request, "cvd/policy.html", _context(_security()))


class AcknowledgementsPage(View):
    def get(self, request):
        sec = _security()
        entries = sorted(sec.acknowledgements, key=lambda a: a.date, reverse=True)
        return render(request, "cvd/acknowledgements.html", _context(sec, entries=entries))
