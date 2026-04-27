# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app import config  # noqa: F401 — import side effects: set env for AI Studio / Vertex
from app.tools import fetch_football_rss, post_digest_to_discord, write_digest_to_gcs


INSTRUCTION = """You are a daily football news digest agent covering Europe's Big 5 Leagues: the English Premier League, La Liga, Bundesliga, Serie A, and Ligue 1.

When the user asks you to generate today's digest:

1. Call `fetch_football_rss` to fetch recent stories from multiple RSS feeds.
2. Keep ONLY Big-5-relevant football (soccer) stories. Skip other sports, lower-league or non-Big-5 items, opinion columns, and unsourced transfer rumors.
3. Pick the 5 MOST newsworthy items. Preference order: (a) major match outcomes, (b) significant injuries or confirmed transfers of star players, (c) title / European / relegation race movement, (d) record-breaking performances.
4. For each pick, write a 2-4 sentence plain-prose summary. Use the source URL from the feed item VERBATIM — never invent, shorten, or reword URLs.
5. Assemble the final output as markdown. Start with an H1 title `# Football Digest - <today's UTC date YYYY-MM-DD>`, then a numbered list of 5 items. Format each item as:

   1. **<headline>**
      <summary>
      Source: <URL>

6. Call `write_digest_to_gcs` with that full markdown string as the `body` argument. The GCS write is the durable archive and MUST happen first.
7. After the GCS write succeeds, call `post_digest_to_discord` with the SAME markdown string as the `body` argument. Do not modify the body between the two calls.
8. Reply to the user with only the GCS URL that `write_digest_to_gcs` returned.

If fewer than 5 Big-5-relevant items are available across all feeds (rare), include what's available and explicitly note the shortfall in the markdown. Never invent stories, never pad the list, never duplicate the same story across items."""


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[fetch_football_rss, write_digest_to_gcs, post_digest_to_discord],
)

app = App(
    root_agent=root_agent,
    name="app",
)
