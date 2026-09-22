"""Console logging for the server, and GraphQL error logging that doesn't flood it.

Vendored byte-identically into every service's settings package; edit one copy and
copy it across (check with ``md5sum */*/logs.py``).

Two rules keep the dev console readable (a flooded console also stalls the server:
``docker compose up`` blocks a container's stdout while the terminal catches up):

* One handler, on root. Django installs its own ``console`` handler on ``django``;
  left alone, every request line prints once there and once more via root.
* A traceback only for a *bug*. An error the code expected and already answers
  (permission denied, not found, invalid input) is one line, never a traceback.
"""

import logging
from typing import Any, Iterable

logger = logging.getLogger("strawberry.execution")

# Third-party loggers that are chatty at INFO and uninteresting unless something breaks.
_QUIET = (
    "django.db.backends",
    "daphne",
    "oauthlib",
    "oauth2_provider",
    "httpx",
    "httpcore",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "asyncio",
    "multipart",
)


def build_logging(level: str = "INFO", rich: bool = False) -> dict:
    """The ``LOGGING`` dict: plain one-line records, or rich rendering when opted in."""
    if rich:
        handler = {
            "class": "rich.logging.RichHandler",
            "formatter": "bare",
            "rich_tracebacks": True,
            "tracebacks_width": 120,
        }
    else:
        handler = {"class": "logging.StreamHandler", "formatter": "plain"}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "datefmt": "%H:%M:%S"},
            # rich prints time and level itself.
            "bare": {"format": "%(message)s"},
        },
        "handlers": {"console": handler},
        "root": {"level": level.upper(), "handlers": ["console"]},
        "loggers": {
            # Replace Django's defaults (a DEBUG-only console handler on ``django``,
            # a separate one on ``django.server``) so each record reaches root once.
            "django": {"handlers": [], "level": "INFO", "propagate": True},
            "django.server": {"handlers": [], "level": "INFO", "propagate": True},
            # Where daphne's runserver logs each request.
            "django.channels.server": {"handlers": [], "level": "INFO", "propagate": True},
            **{name: {"handlers": [], "level": "WARNING", "propagate": True} for name in _QUIET},
        },
    }


def _expected_errors() -> tuple[type[BaseException], ...]:
    from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
    from graphql import GraphQLError

    # ValueError covers pydantic's ValidationError; LookupError covers kante's UnscopedModelError.
    return (GraphQLError, PermissionError, PermissionDenied, ObjectDoesNotExist, ValidationError, ValueError, LookupError)


def log_graphql_errors(errors: Iterable[Any]) -> None:
    """Log resolver errors: one line for an expected refusal, a traceback for a bug."""
    expected = _expected_errors()
    for error in errors:
        original = getattr(error, "original_error", None)
        path = ".".join(str(p) for p in (error.path or ())) or "-"
        if original is None or isinstance(original, expected):
            # Parse/validation errors (no original) and deliberate refusals: the client already has the message.
            logger.info("GraphQL %s: %s", path, error.message)
        else:
            logger.error("GraphQL %s: %s", path, error.message, exc_info=original)


class QuietErrorsSchema:
    """Mix in before the strawberry schema class: ``class Schema(QuietErrorsSchema, kante.Schema)``."""

    def process_errors(self, errors: list, execution_context: Any = None) -> None:
        log_graphql_errors(errors)
