"""Tools for the football news agent: RSS fetching, GCS write, Discord post."""

import datetime
import json
import logging
import os
import urllib.error
import urllib.request

import feedparser
from google.cloud import storage

from app.config import FEEDS

logger = logging.getLogger(__name__)

DISCORD_MAX_CHARS = 1900
DISCORD_USER_AGENT = "football-news-agent/0.1 (+https://github.com/anthropic; urllib)"


def fetch_football_rss() -> list[dict]:
    """Fetch recent football stories from curated Big-5-Leagues RSS feeds.

    Returns a deduplicated list of items (newest-first by ``published``).
    One bad feed must not block the run: feeds that fail to parse are skipped
    with a WARNING log.

    Returns:
        A list of item dicts with keys: ``title``, ``link``, ``summary``,
        ``published``, ``source``.
    """
    items: dict[str, dict] = {}
    for feed_url, source_name in FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
            if parsed.bozo and not parsed.entries:
                logger.warning(
                    "Feed parse failed: %s (%s)",
                    source_name,
                    getattr(parsed, "bozo_exception", "unknown"),
                )
                continue
            for entry in parsed.entries:
                link = entry.get("link")
                if not link or link in items:
                    continue
                items[link] = {
                    "title": entry.get("title", ""),
                    "link": link,
                    "summary": entry.get("summary", ""),
                    "published": entry.get("published", ""),
                    "source": source_name,
                }
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", source_name, e)

    result = sorted(
        items.values(), key=lambda it: it.get("published", ""), reverse=True
    )
    logger.info("fetched %d unique items across %d feeds", len(result), len(FEEDS))
    return result


def write_digest_to_gcs(body: str) -> str:
    """Upload the markdown digest to the configured GCS bucket.

    The bucket name is read from the ``DIGEST_BUCKET`` env var — never from a
    tool argument — so a compromised RSS item cannot redirect output. The
    object name is ``YYYY-MM-DD-HHMMSS.md`` in UTC.

    Args:
        body: The full markdown content of the digest.

    Returns:
        The ``gs://`` URL of the written object.
    """
    bucket_name = os.environ.get("DIGEST_BUCKET")
    if not bucket_name:
        raise RuntimeError(
            "DIGEST_BUCKET env var is not set. Set it in .env (local) or in"
            " Cloud Run configuration (deployed)."
        )
    now = datetime.datetime.now(datetime.UTC)
    object_name = now.strftime("%Y-%m-%d-%H%M%S") + ".md"
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(body, content_type="text/markdown; charset=utf-8")
    gs_url = f"gs://{bucket_name}/{object_name}"
    logger.info("wrote digest to %s (%d bytes)", gs_url, len(body.encode("utf-8")))
    return gs_url


def _strip_source_lines(body: str) -> str:
    """Drop lines that start (after optional whitespace) with ``Source:``.

    Discord posts get the prose-only digest; the GCS object retains the full
    sourced version. Also collapses any runs of 3+ blank lines that result.
    """
    kept = [
        line
        for line in body.splitlines()
        if not line.lstrip().lower().startswith("source:")
    ]
    out = "\n".join(kept)
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip() + "\n"


def _chunk_for_discord(body: str) -> list[str]:
    """Split markdown into <=DISCORD_MAX_CHARS chunks, breaking on blank lines."""
    if len(body) <= DISCORD_MAX_CHARS:
        return [body]
    chunks: list[str] = []
    current = ""
    for block in body.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= DISCORD_MAX_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(block) <= DISCORD_MAX_CHARS:
            current = block
        else:
            for i in range(0, len(block), DISCORD_MAX_CHARS):
                chunks.append(block[i : i + DISCORD_MAX_CHARS])
            current = ""
    if current:
        chunks.append(current)
    return chunks


def post_digest_to_discord(body: str) -> str:
    """Post the markdown digest to the configured Discord channel via webhook.

    The webhook URL is read from ``DISCORD_WEBHOOK_URL`` — never from a tool
    argument — so a compromised RSS item cannot redirect output. ``Source:``
    lines are stripped before posting (they remain in the GCS archive).
    Discord's 2000-char message limit is handled by splitting on blank-line
    boundaries. Discord/Cloudflare blocks the default Python-urllib UA, so a
    custom ``User-Agent`` header is required.

    Args:
        body: The full markdown content of the digest, with ``Source:`` lines.

    Returns:
        A short status string of the form ``"posted N message(s) to discord"``.
    """
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL env var is not set. Set it in .env (local) or"
            " on the Agent Runtime deployment (deployed)."
        )
    discord_body = _strip_source_lines(body)
    chunks = _chunk_for_discord(discord_body)
    for chunk in chunks:
        payload = json.dumps({"content": chunk}).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": DISCORD_USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(
                    f"discord webhook returned HTTP {response.status}"
                )
    logger.info("posted %d message(s) to discord", len(chunks))
    return f"posted {len(chunks)} message(s) to discord"
