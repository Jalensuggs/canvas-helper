import logging
from pathlib import Path

import pytest

from canvas_helper.redact import RedactingFilter, redact, redact_text
from canvas_helper.security import safe_join, sanitize_html


def test_redacts_credentials_and_sensitive_query_values():
    text = (
        "Authorization: Bearer abc.def "
        "https://x.test/a?access_token=canvas-secret&verifier=verify-secret "
        "sk-ant-api-secret"
    )
    cleaned = redact_text(text)
    assert "abc.def" not in cleaned
    assert "canvas-secret" not in cleaned
    assert "verify-secret" not in cleaned
    assert "sk-ant-api-secret" not in cleaned
    assert redact({"token": "x", "nested": {"api_key": "y"}}) == {
        "token": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]"},
    }

    record = logging.LogRecord("test", logging.INFO, "", 0, "Bearer hidden", (), None)
    assert RedactingFilter().filter(record)
    assert "hidden" not in record.msg


def test_safe_join_blocks_traversal_and_symlink_escape(tmp_path: Path):
    root = tmp_path / "materials"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_join(root, "..")
    with pytest.raises(ValueError):
        safe_join(root, "../outside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_join(root, "link", "file.txt")

    assert safe_join(root, "Course: 1", "notes.pdf").is_relative_to(root)


def test_html_sanitization_removes_active_content():
    dirty = '<script>alert(1)</script><a href="javascript:alert(2)" onclick="x()">ok</a>'
    clean = sanitize_html(dirty)
    assert "<script" not in clean
    assert "javascript:" not in clean
    assert "onclick" not in clean
