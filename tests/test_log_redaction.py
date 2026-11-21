"""Per-meeting audio tokens must never reach the logs."""

import logging

from revops_buddy.server import RedactTokensFilter


def test_websocket_log_line_token_is_redacted():
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        '%s - "WebSocket %s" [accepted]',
        ("100.64.0.10:51452", "/api/mb/out?session_id=abc&token=SECRETvalue-123_x"),
        None,
    )
    assert RedactTokensFilter().filter(record) is True
    line = record.getMessage()
    assert "SECRETvalue" not in line
    assert "token=[redacted]" in line and "session_id=abc" in line
