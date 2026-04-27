# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for app.tools — focused on the Discord webhook tool."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.tools import (
    DISCORD_MAX_CHARS,
    _chunk_for_discord,
    _strip_source_lines,
    post_digest_to_discord,
)


def test_chunk_for_discord_short_body_single_chunk() -> None:
    body = "# Football Digest\n\n1. **Hello** — short."
    assert _chunk_for_discord(body) == [body]


def test_chunk_for_discord_splits_on_blank_lines() -> None:
    block = "x" * 800
    body = "\n\n".join([block, block, block])  # 3 blocks, ~2400 chars
    chunks = _chunk_for_discord(body)
    assert len(chunks) >= 2
    assert all(len(c) <= DISCORD_MAX_CHARS for c in chunks)
    rejoined = "\n\n".join(chunks)
    for block_text in [block]:
        assert rejoined.count(block_text) == 3


def test_chunk_for_discord_handles_oversized_block() -> None:
    body = "y" * (DISCORD_MAX_CHARS * 2 + 50)
    chunks = _chunk_for_discord(body)
    assert all(len(c) <= DISCORD_MAX_CHARS for c in chunks)
    assert "".join(chunks) == body


def test_post_digest_to_discord_raises_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(RuntimeError, match="DISCORD_WEBHOOK_URL"):
        post_digest_to_discord("anything")


def test_strip_source_lines_removes_source_and_collapses_blanks() -> None:
    body = (
        "# Football Digest\n\n"
        "1. **Headline 1**\n"
        "   summary one.\n"
        "   Source: https://example.com/a\n\n"
        "2. **Headline 2**\n"
        "   summary two.\n"
        "   source: https://example.com/b\n"
    )
    out = _strip_source_lines(body)
    assert "Source:" not in out
    assert "source:" not in out
    assert "https://example.com/a" not in out
    assert "https://example.com/b" not in out
    assert "**Headline 1**" in out
    assert "**Headline 2**" in out
    assert "summary one." in out
    assert "summary two." in out
    assert "\n\n\n" not in out


def test_post_digest_to_discord_strips_sources_and_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    body = (
        "# Football Digest\n\n"
        "1. **Story** — summary.\n"
        "   Source: https://example.com/secret-link"
    )

    fake_response = MagicMock()
    fake_response.status = 204
    fake_response.__enter__ = lambda self: self
    fake_response.__exit__ = lambda self, *a: None

    with patch("app.tools.urllib.request.urlopen", return_value=fake_response) as mock_open:
        result = post_digest_to_discord(body)

    assert mock_open.call_count == 1
    request = mock_open.call_args.args[0]
    assert request.full_url == "https://discord.com/api/webhooks/x/y"
    assert request.get_method() == "POST"
    payload = json.loads(request.data.decode("utf-8"))
    assert "https://example.com/secret-link" not in payload["content"]
    assert "Source:" not in payload["content"]
    assert "**Story**" in payload["content"]
    # Discord/Cloudflare blocks the default Python-urllib UA with code 1010,
    # so the tool MUST send a custom User-Agent header. Don't remove this.
    ua = request.get_header("User-agent")
    assert ua and "Python-urllib" not in ua
    assert "1 message" in result


def test_post_digest_to_discord_chunks_long_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    block = "z" * 1000
    body = "\n\n".join([block] * 4)  # ~4000 chars, will need >=2 chunks

    fake_response = MagicMock()
    fake_response.status = 204
    fake_response.__enter__ = lambda self: self
    fake_response.__exit__ = lambda self, *a: None

    with patch("app.tools.urllib.request.urlopen", return_value=fake_response) as mock_open:
        result = post_digest_to_discord(body)

    assert mock_open.call_count >= 2
    assert f"{mock_open.call_count} message" in result
