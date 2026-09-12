from urllib.parse import urlparse

from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.conf import settings


def get_frontend_url():
    """Get the frontend URL from settings or default."""
    return getattr(settings, 'KONTROL_FRONTEND_URL', '/')


def safe_redirect_target(candidate: str | None) -> str:
    """Return ``candidate`` only if it is a safe local redirect target.

    ``change_org`` passed a caller-supplied ``next`` straight to ``redirect()``,
    which is an open redirect: an attacker gets a link on the deployment's own
    domain that bounces the victim to theirs, which is exactly what makes a
    phishing page credible. Only same-site paths, or the configured frontend
    origin, are accepted; anything else falls back to the frontend root.
    """
    # `KONTROL_FRONTEND_URL` can be configured empty, and `redirect("")` is not a
    # usable response — fall back to the site root in that case.
    fallback = get_frontend_url() or "/"
    if not candidate:
        return fallback

    parsed = urlparse(candidate)
    # A root-relative path with no scheme/netloc is same-origin by construction.
    # `//evil.tld` parses with an empty scheme but a non-empty netloc, so testing
    # netloc (not just scheme) is what makes this safe.
    if not parsed.scheme and not parsed.netloc and candidate.startswith("/"):
        return candidate

    frontend = urlparse(fallback)
    if (parsed.scheme, parsed.netloc) == (frontend.scheme, frontend.netloc) and parsed.netloc:
        return candidate

    return fallback


@login_required
def organization_detail(request, slug):
    """Redirect to frontend organization page."""
    frontend_url = get_frontend_url()
    return redirect(f"{frontend_url}/organizations/{slug}")


@login_required
def change_org(request):
    """API endpoint to change the active organization."""
    from karakter.models import Organization

    if request.method == "POST":
        org_slug = request.POST.get("organization")
        try:
            # Only organizations the user actually belongs to. Previously any slug
            # was accepted, so a user could write an organization they are not a
            # member of into their own row. The token path fails closed on that
            # (AuthAppExtension resolves the membership, and refuses when there is
            # none), so this was not a privilege escalation — but persisting a
            # relationship that does not exist is still wrong, and it produced a
            # user whose active organization silently fails every request.
            organization = Organization.objects.get(
                slug=org_slug, memberships__user=request.user
            )
            request.user.active_organization = organization
            request.user.save()
            return redirect(safe_redirect_target(request.POST.get("next")))
        except Organization.DoesNotExist:
            return JsonResponse({"error": "Organization does not exist."}, status=404)

    # For GET requests, redirect to frontend
    frontend_url = get_frontend_url()
    return redirect(f"{frontend_url}/organizations")


@login_required
def accept_invite(request, token):
    """View for accepting an organization invite - redirects to frontend with token."""
    from karakter.models import Invite, Membership, Role

    try:
        invite = Invite.objects.get(token=token)
    except Invite.DoesNotExist:
        frontend_url = get_frontend_url()
        return redirect(f"{frontend_url}/invite/error?reason=invalid")

    # Check status and validity
    if invite.status != Invite.Status.PENDING:
        frontend_url = get_frontend_url()
        return redirect(f"{frontend_url}/invite/error?reason=already_processed")

    if not invite.is_valid():
        frontend_url = get_frontend_url()
        return redirect(f"{frontend_url}/invite/error?reason=expired")

    # Handle POST request (user accepts or declines the invite)
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "decline":
            invite.decline(request.user)
            frontend_url = get_frontend_url()
            return redirect(frontend_url)

        # Default action is accept
        # Check if already a member
        existing_membership = Membership.objects.filter(
            user=request.user,
            organization=invite.created_for,
        ).first()

        if existing_membership:
            invite.accept(request.user)
            request.user.active_organization = invite.created_for
            request.user.save()
            frontend_url = get_frontend_url()
            return redirect(frontend_url)

        # Create new membership
        membership = Membership.objects.create(
            user=request.user,
            organization=invite.created_for,
        )

        # Assign roles from the invite
        invite_roles = invite.roles.all()
        if invite_roles.exists():
            membership.roles.set(invite_roles)
        else:
            # Fallback to guest role if invite has no roles
            try:
                guest_role = Role.objects.get(identifier="guest", organization=invite.created_for)
                membership.roles.add(guest_role)
            except Role.DoesNotExist:
                pass

        # Mark invite as accepted
        invite.accept(request.user)

        # Set as active organization and redirect
        request.user.active_organization = invite.created_for
        request.user.save()

        frontend_url = get_frontend_url()
        return redirect(frontend_url)

    # For GET requests, redirect to frontend invite page
    frontend_url = get_frontend_url()
    return redirect(f"{frontend_url}/invite/{token}")


@login_required
def leave_org(request, slug):
    """API endpoint to leave an organization."""
    from karakter.models import Organization, Membership

    if request.method == "POST":
        try:
            organization = Organization.objects.get(slug=slug)
            membership = Membership.objects.get(user=request.user, organization=organization)
            membership.delete()

            # If the user was active in this org, unset it
            if request.user.active_organization == organization:
                request.user.active_organization = None
                request.user.save()

            frontend_url = get_frontend_url()
            return redirect(frontend_url)
        except (Organization.DoesNotExist, Membership.DoesNotExist):
            return JsonResponse({"error": "Organization or membership not found."}, status=404)

    return redirect("organization_detail", slug=slug)
