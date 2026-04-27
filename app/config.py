"""Centralized configuration for the football news agent.

Auto-detects AI Studio (via ``GOOGLE_API_KEY``) vs Vertex AI (via ADC),
following the same pattern as the ``ambient-expense-agent`` ADK sample.
Also exposes the Big-5-Leagues RSS feed list.
"""

import os

from dotenv import load_dotenv

load_dotenv()

if os.getenv("GOOGLE_API_KEY"):
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "False")
else:
    import google.auth

    _, project_id = google.auth.default()
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id or "")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")


FEEDS: list[tuple[str, str]] = [
    ("http://feeds.bbci.co.uk/sport/football/rss.xml", "BBC Sport"),
    ("https://www.theguardian.com/football/rss", "Guardian Football"),
    ("https://www.espn.com/espn/rss/soccer/news", "ESPN FC"),
    ("https://www.90min.com/posts.rss", "90min"),
    ("https://www.goal.com/feeds/news", "Goal.com"),
]
