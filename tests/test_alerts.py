"""Tests for execution/alerts.py -- email alerting via the Resend HTTP API
(sprint v8.6). Uses the HTTP API, not SMTP, because Render blocks outbound
SMTP ports on free-tier services.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from execution.alerts import RESEND_API_URL, send_alert_email


def test_send_skipped_when_unconfigured(monkeypatch) -> None:
    """No API call is attempted when credentials are missing."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    monkeypatch.delenv("ALLOWED_EMAIL", raising=False)

    with patch("requests.post") as mock_post:
        ok = send_alert_email("subject", "body")

    assert ok is False
    mock_post.assert_not_called()


def test_send_falls_back_to_allowed_email(monkeypatch) -> None:
    """ALERT_EMAIL_TO is optional -- falls back to ALLOWED_EMAIL."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    monkeypatch.setenv("ALLOWED_EMAIL", "operator@example.com")
    monkeypatch.delenv("RESEND_FROM", raising=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_resp) as mock_post:
        ok = send_alert_email("subject", "body")

    assert ok is True
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == RESEND_API_URL
    assert call.kwargs["headers"]["Authorization"] == "Bearer re_test_key"
    assert call.kwargs["json"]["to"] == ["operator@example.com"]
    assert call.kwargs["json"]["subject"] == "subject"
    assert call.kwargs["json"]["text"] == "body"


def test_send_uses_custom_from_when_set(monkeypatch) -> None:
    """RESEND_FROM overrides the default shared sender."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("ALERT_EMAIL_TO", "operator@example.com")
    monkeypatch.setenv("RESEND_FROM", "alerts@mydomain.com")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    with patch("requests.post", return_value=mock_resp) as mock_post:
        send_alert_email("subject", "body")

    assert mock_post.call_args.kwargs["json"]["from"] == "alerts@mydomain.com"


def test_send_returns_false_on_api_error(monkeypatch) -> None:
    """Any request failure is swallowed -- returns False, never raises."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("ALERT_EMAIL_TO", "operator@example.com")

    with patch("requests.post", side_effect=OSError("network unreachable")):
        ok = send_alert_email("subject", "body")

    assert ok is False


def test_send_returns_false_on_non_2xx_status(monkeypatch) -> None:
    """A non-2xx response (e.g. bad API key) is treated as failure."""
    monkeypatch.setenv("RESEND_API_KEY", "re_bad_key")
    monkeypatch.setenv("ALERT_EMAIL_TO", "operator@example.com")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("401 Unauthorized")
    with patch("requests.post", return_value=mock_resp):
        ok = send_alert_email("subject", "body")

    assert ok is False
