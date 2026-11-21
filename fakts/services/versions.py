"""Whether a service's version is within what a client asked for. Advisory, never a gate."""

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


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
