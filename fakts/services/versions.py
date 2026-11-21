"""Whether a service's version is within what a client asked for. Advisory, never a gate."""

from fakts import models
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


#: What an instance runs when nobody can say: a source build. Never a release.
LOCAL = "0+local"
#: What installers wrote for every service before they knew its version.
PLACEHOLDER = "1.0.0"


def declared(version: str | None) -> str:
    """The version a hub's manifest names for a service, as it is kept.

    The placeholder is no claim: it becomes ``LOCAL`` until the hub reports what runs.
    """
    return LOCAL if not version or version == PLACEHOLDER else version


def release_for(service: "models.Service", version: str | None, instance: "models.ServiceInstance | None") -> "models.ServiceRelease":
    """The release a hub's manifest puts an instance on.

    A manifest that names no version leaves a known one alone: what the service reported
    it runs outlasts an authorization by an installer that could not tell.
    """
    wanted = declared(version)
    if wanted == LOCAL and instance is not None and instance.service_id == service.pk:
        return instance.release
    release, _ = models.ServiceRelease.objects.get_or_create(service=service, version=wanted)
    return release


def known(version: str | None) -> str | None:
    """``version`` if it names a release; None for a local build or what cannot be read."""
    try:
        return version if version and Version(version).local is None else None
    except InvalidVersion:
        return None


def satisfies(version: str | None, specifier: str | None) -> bool | None:
    """True or False when both are known and readable, None otherwise.

    ``specifier`` is a PEP 440 specifier set over semver versions (``>=12.1,<14``). A
    pre-release counts as its release line, so a hub on ``12.2.0-rc.1`` meets ``>=12.1``.
    A local build (``0+local``) is of no known release: None.
    """
    if not version or not specifier:
        return None
    try:
        running = Version(version)
        if running.local is not None:
            return None
        return SpecifierSet(specifier).contains(running, prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return None
