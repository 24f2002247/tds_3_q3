"""
Tests for diagnostic logging in DecisionEngine._ai_decide() and _fallback_proposal().

Ground rules verified here:
- Attempt number is logged on every failure.
- HTTP status code and error category are logged for httpx.HTTPStatusError.
- Exception *type* (not message/content) is logged for other errors.
- _fallback_proposal logs the reason category and chosen action.
- No credential strings (AI_API_KEY, 'Bearer', 'Authorization') appear in any log record.
- No raw dossier content or provider error bodies appear in any log record.
"""
import json
import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.decision_engine import (
    DecisionEngine,
    _exc_category,
    _fallback_category,
    _http_error_category,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

ALL_ACTIONS = {
    "create_draft", "update_internal_record", "send_approved_notice",
    "request_confirmation", "quarantine_item", "no_action",
}

DOSSIER = {
    "dossierId": "test-dossier-1",
    "mailbox": "support@example.com",
    "sources": [
        {
            "sourceId": "s1",
            "kind": "email",
            "provenance": "customer",
            "title": "Test mail",
            "lines": [{"lineId": "l1", "text": "Please help with my order"}],
        }
    ],
}

CALL_ID = "call-1234567890ab"

# The key value used in all tests — chosen to be obviously fake
_FAKE_KEY = "sk-test-key-logging-tests"


def _ok_response(action: str = "no_action") -> MagicMock:
    """Mock httpx.Response that returns a valid proposal JSON for the given action."""
    proposal = {
        "dossierId": DOSSIER["dossierId"],
        "callId": CALL_ID,
        "action": action,
        "target": None,
        "payload": {"reasonCode": "INFORMATIONAL", "referenceId": "ref-0001"},
        "evidence": ["l1"],
    }
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={"choices": [{"message": {"content": json.dumps(proposal)}}]}
    )
    return resp


def _http_error(status: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError for the given status code."""
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


def _run(monkeypatch, caplog, mock_post):
    """
    Inject env vars so get_settings() (lru_cache) sees the fake key,
    patch httpx.post at the module level, capture logs, call _ai_decide.
    """
    # Clear the lru_cache so our env vars take effect
    from app.config import get_settings
    get_settings.cache_clear()

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("AI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")

    engine = DecisionEngine(provider="openai")

    with patch("app.decision_engine.httpx.post", mock_post):
        with caplog.at_level(logging.DEBUG, logger="app.decision_engine"):
            result = engine._ai_decide(DOSSIER, ALL_ACTIONS, CALL_ID)

    # Always restore the cache so other tests aren't affected
    get_settings.cache_clear()
    return result, caplog.records


# ---------------------------------------------------------------------------
# Unit tests for pure helper functions (no I/O)
# ---------------------------------------------------------------------------

def test_exc_category_returns_type_name():
    assert _exc_category(ValueError("secret prompt content here")) == "ValueError"
    assert _exc_category(RuntimeError("AI_API_KEY=sk-xyz")) == "RuntimeError"
    assert _exc_category(KeyError("Authorization")) == "KeyError"


def test_http_error_category_covers_key_codes():
    for status, expected in [
        (400, "bad_request"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (429, "rate_limited"),
        (500, "server_error"),
        (503, "service_unavailable"),
        (418, "client_error_418"),
        (502, "server_error_502"),
    ]:
        exc = _http_error(status)
        assert _http_error_category(exc) == expected, f"unexpected category for status {status}"


def test_fallback_category_covers_known_patterns():
    assert _fallback_category("model returned disallowed action: steal_data") == "disallowed_action"
    assert _fallback_category("validation failure in schema") == "validation_failure"
    assert _fallback_category("AI failed after 3 attempts: Timeout") == "exhausted_retries"
    assert _fallback_category("some unknown provider issue") == "ai_error"


# ---------------------------------------------------------------------------
# HTTP error logging
# ---------------------------------------------------------------------------

class TestHttpErrorLogging:
    def test_rate_limit_logs_status_and_category(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(429)))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        rate_limit_logs = [t for t in warnings if "status=429" in t]
        assert len(rate_limit_logs) == 3, f"expected 3 attempt warnings; got: {warnings}"
        for text in rate_limit_logs:
            assert "category=rate_limited" in text
            assert "attempt=" in text

    def test_unauthorized_logs_category(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(401)))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        assert any("category=unauthorized" in t for t in warnings), \
            f"expected unauthorized category; got: {warnings}"

    def test_attempt_numbers_are_1_2_3(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(500)))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        http_logs = [t for t in warnings if "status=500" in t]
        assert len(http_logs) == 3, f"expected 3 HTTP-error logs; got: {warnings}"
        for n in ("1/", "2/", "3/"):
            assert any(f"attempt={n}" in t for t in http_logs), \
                f"attempt={n} not found in: {http_logs}"

    def test_exhaustion_logs_error_level(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(503)))

        errors = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
        assert errors, f"expected at least one ERROR log after exhausting retries; got: {records}"
        assert any("invoking fallback" in t for t in errors)


# ---------------------------------------------------------------------------
# Non-HTTP exception logging
# ---------------------------------------------------------------------------

class TestNonHttpErrorLogging:
    def test_timeout_logs_exc_type_not_message(self, monkeypatch, caplog):
        result, records = _run(
            monkeypatch, caplog,
            MagicMock(side_effect=httpx.TimeoutException("connect timeout to https://secret-host/"))
        )
        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        # Per-attempt lines contain both the type name and "attempt="
        timeout_attempt_logs = [t for t in warnings if "TimeoutException" in t and "attempt=" in t]
        assert len(timeout_attempt_logs) == 3, \
            f"expected 3 per-attempt timeout logs; got: {warnings}"
        # The exception message must NOT appear in any log line
        for text in warnings:
            assert "connect timeout" not in text.lower(), \
                "exception message leaked into log"
            assert "secret-host" not in text, \
                "exception message details leaked into log"

    def test_json_decode_error_logs_exc_type(self, monkeypatch, caplog):
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 200
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(
            return_value={"choices": [{"message": {"content": "not-valid-json-{"}}]}
        )
        result, records = _run(monkeypatch, caplog, MagicMock(return_value=bad_resp))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        assert any("JSONDecodeError" in t for t in warnings), \
            f"expected JSONDecodeError in warnings; got: {warnings}"
        # Raw content must not appear
        for text in warnings:
            assert "not-valid-json" not in text


# ---------------------------------------------------------------------------
# Disallowed action logging
# ---------------------------------------------------------------------------

class TestDisallowedActionLogging:
    def test_disallowed_action_logged_with_action_name(self, monkeypatch, caplog):
        bad_proposal = {
            "dossierId": DOSSIER["dossierId"],
            "callId": CALL_ID,
            "action": "delete_everything",   # not in allowed set
            "target": None,
            "payload": {},
            "evidence": ["l1"],
        }
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 200
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(
            return_value={"choices": [{"message": {"content": json.dumps(bad_proposal)}}]}
        )
        result, records = _run(monkeypatch, caplog, MagicMock(return_value=bad_resp))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        assert any("disallowed" in t and "delete_everything" in t for t in warnings), \
            f"expected disallowed-action warning; got: {warnings}"

    def test_disallowed_action_falls_back_to_allowed_action(self, monkeypatch, caplog):
        bad_proposal = {
            "dossierId": DOSSIER["dossierId"],
            "callId": CALL_ID,
            "action": "not_a_real_action",
            "target": None,
            "payload": {},
            "evidence": ["l1"],
        }
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 200
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(
            return_value={"choices": [{"message": {"content": json.dumps(bad_proposal)}}]}
        )
        result, records = _run(monkeypatch, caplog, MagicMock(return_value=bad_resp))
        assert result["action"] in ALL_ACTIONS, \
            f"fallback must produce an allowed action; got: {result['action']}"


# ---------------------------------------------------------------------------
# Fallback logging
# ---------------------------------------------------------------------------

class TestFallbackLogging:
    def test_fallback_logs_category_and_chosen_action(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(429)))

        warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        fallback_logs = [t for t in warnings if "fallback_proposal" in t]
        assert fallback_logs, f"no fallback_proposal log found; all warnings: {warnings}"
        assert any("category=" in t for t in fallback_logs), \
            "fallback log must include a category= field"
        assert any("no_action" in t or "create_draft" in t for t in fallback_logs), \
            "fallback log must name the chosen action"

    def test_fallback_does_not_log_raw_dossier_content(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(500)))

        all_texts = [r.getMessage() for r in records]
        for text in all_texts:
            assert "Please help with" not in text, \
                f"raw dossier line text leaked into log: {text!r}"
            assert "support@example.com" not in text, \
                f"mailbox address leaked into log: {text!r}"


# ---------------------------------------------------------------------------
# No credential leakage
# ---------------------------------------------------------------------------

class TestNoCredentialLeakage:
    def test_api_key_never_logged_on_success(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(return_value=_ok_response("no_action")))

        combined = " ".join(r.getMessage() for r in records)
        assert _FAKE_KEY not in combined, "API key appeared in log records"
        assert "Bearer" not in combined, "'Bearer' token prefix appeared in log records"
        assert "Authorization" not in combined, "Authorization header appeared in log records"

    def test_api_key_never_logged_on_failure(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(401)))

        combined = " ".join(r.getMessage() for r in records)
        assert _FAKE_KEY not in combined, "API key appeared in log records on failure"
        assert "Bearer" not in combined
        assert "Authorization" not in combined

    def test_system_prompt_never_logged(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(side_effect=_http_error(500)))

        combined = " ".join(r.getMessage() for r in records)
        # Spot-check distinctive phrases from SYSTEM_PROMPT / ACTION_SPECS
        assert "AUTHORITY RULES" not in combined, "system prompt content leaked into log"
        assert "EVIDENCE RULES" not in combined
        assert "ALLOWED ACTIONS" not in combined


# ---------------------------------------------------------------------------
# Success path: debug log
# ---------------------------------------------------------------------------

class TestSuccessPathDebugLog:
    def test_accepted_proposal_logged_at_debug(self, monkeypatch, caplog):
        result, records = _run(monkeypatch, caplog, MagicMock(return_value=_ok_response("no_action")))

        debug_texts = [r.getMessage() for r in records if r.levelno == logging.DEBUG]
        assert any("proposal accepted" in t and "no_action" in t for t in debug_texts), \
            f"expected debug acceptance log with action name; got: {debug_texts}"
        assert result["action"] == "no_action"
