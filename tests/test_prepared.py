"""What an installer relies on when it runs this image's ``migrate`` job.

The rules are every service's: https://github.com/arkitektio/arkitekt-service/blob/main/docs/migrations-and-jobs.md
"""

from __future__ import annotations

import pytest
from arkitekt_service import prepared

from lok_server.contract import contract


@pytest.fixture(autouse=True)
def this_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract is this service's."""
    monkeypatch.setenv("ARKITEKT_SERVICE", "lok_server.contract")


@pytest.mark.django_db
def test_every_model_change_has_its_migration() -> None:
    """No model differs from what the committed migrations make of it."""
    prepared.migrations_are_committed()


def test_every_job_is_a_command_of_this_service() -> None:
    """A job that names no command would fail the installer that runs it."""
    prepared.jobs_are_commands(contract)


@pytest.mark.django_db
def test_the_setup_is_safe_to_run_again() -> None:
    """``migrate`` runs it for every build."""
    prepared.setup_runs_again(contract)
