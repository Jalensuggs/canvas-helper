"""Deployment-time settings parsing and logging survival.

Both of these only misbehave in a real deployment: a .env sourced by a shell
strips the quotes out of a JSON list, and running migrations inside the app's
own startup used to silence every logger for the rest of the process.
"""

import asyncio
import logging

import pytest
from pydantic import ValidationError

from canvas_helper.config import Settings
from canvas_helper.db import migrate_database
from canvas_helper.redact import RedactingFilter, configure_logging


@pytest.mark.parametrize(
    "raw",
    [
        "student.uts.edu.au,uts.edu.au",
        "student.uts.edu.au, uts.edu.au",
        '["student.uts.edu.au","uts.edu.au"]',
        # What a shell-sourced .env actually passes through: the quotes are
        # eaten before the process ever sees the value.
        "[student.uts.edu.au,uts.edu.au]",
    ],
)
def test_email_allowlist_accepts_every_plausible_spelling(monkeypatch, raw):
    monkeypatch.setenv("CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS", raw)
    settings = Settings()
    assert settings.allowed_email_domains == ("student.uts.edu.au", "uts.edu.au")
    assert settings.email_domain_allowed("me@uts.edu.au")
    assert not settings.email_domain_allowed("me@gmail.com")


def test_email_allowlist_normalizes_case_and_at_prefix(monkeypatch):
    monkeypatch.setenv("CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS", "@Student.UTS.edu.AU")
    settings = Settings()
    assert settings.allowed_email_domains == ("student.uts.edu.au",)
    assert settings.email_domain_allowed("ME@student.uts.EDU.au")


def test_empty_allowlist_permits_everything(monkeypatch):
    monkeypatch.setenv("CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS", "")
    assert Settings().email_domain_allowed("anyone@anywhere.test")


@pytest.mark.parametrize("raw", ["3,9", "[3,9]", '["3","9"]'])
def test_term_start_months_accept_both_forms(monkeypatch, raw):
    monkeypatch.setenv("CANVAS_HELPER_TERM_START_MONTHS", raw)
    assert Settings().term_start_months == (3, 9)


def test_invalid_term_month_is_rejected(monkeypatch):
    monkeypatch.setenv("CANVAS_HELPER_TERM_START_MONTHS", "13")
    with pytest.raises(ValidationError, match="between 1 and 12"):
        Settings()


def test_unparseable_term_month_is_rejected(monkeypatch):
    monkeypatch.setenv("CANVAS_HELPER_TERM_START_MONTHS", "spring")
    with pytest.raises((ValidationError, ValueError)):
        Settings()


def redacting_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if any(isinstance(item, RedactingFilter) for item in handler.filters)
    ]


def test_migrations_do_not_silence_application_logging(tmp_path):
    """Alembic's fileConfig defaults to disable_existing_loggers=True.

    The app migrates during its own startup, so that default used to disable
    uvicorn's access log and every canvas_helper logger for the life of the
    process — including the error that tells an operator SMTP is misconfigured.
    """
    previous = logging.getLogger().handlers[:]
    try:
        configure_logging()
        assert len(redacting_handlers()) == 1

        marker = logging.getLogger("canvas_helper.probe")
        assert marker.isEnabledFor(logging.ERROR)

        asyncio.run(
            migrate_database(f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}")
        )

        assert len(redacting_handlers()) == 1, "redacting handler was replaced"
        assert marker.isEnabledFor(logging.ERROR), "logger was disabled by alembic"
        assert not marker.disabled
    finally:
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in previous:
            root.addHandler(handler)


def test_redaction_scrubs_credentials_and_addresses():
    import io

    handler = logging.StreamHandler(io.StringIO())
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("canvas_helper.redaction_probe")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.error("contact alice@example.com with Bearer abc123DEFghi456jklmno")
        output = handler.stream.getvalue()
    finally:
        logger.removeHandler(handler)
    assert "alice@example.com" not in output
    assert "abc123DEFghi456jklmno" not in output
    assert "[REDACTED]" in output
