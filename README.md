# Football Content Agent

A daily digest agent that fetches the top 5 Big-5-Leagues European football stories from RSS, writes a markdown archive to Google Cloud Storage, and posts the same digest to a Discord channel — all triggered by a daily Cloud Scheduler cron.

Built on **Google's [Agent Development Kit (ADK)](https://google.github.io/adk-docs/)** and deployed to **Vertex AI Agent Runtime** (Gemini Enterprise Agent Platform). Scaffolded, evaluated, and deployed end-to-end with the [`agents-cli`](https://pypi.org/project/google-agents-cli/) toolchain.

## Architecture

```
Cloud Scheduler  (0 7 * * * America/New_York, OAuth → SA)
       │
       │ POST :streamQuery
       ▼
Vertex AI Agent Runtime  (ADK LlmAgent, scaled to zero between runs)
       │
       ├──► Gemini Flash on Vertex AI       (model calls; 5 RSS feeds → top 5 → summaries)
       ├──► Cloud Storage bucket            (durable archive, sources retained, UTC-named)
       └──► Discord webhook                 (human-facing, sources stripped, auto-chunked)
```

## Stack

| Component | Tool |
|---|---|
| Agent framework | Google ADK (`google-adk`) — `LlmAgent` with three Python tools |
| Runtime | Vertex AI Agent Runtime / Reasoning Engines |
| Model | Gemini Flash via Vertex AI (with AI Studio fallback for local dev) |
| Trigger | Cloud Scheduler HTTP job, OAuth-authenticated to a dedicated SA |
| Archive | Cloud Storage (`google-cloud-storage`) |
| Notification | Discord incoming webhook (stdlib `urllib.request`, no extra deps) |
| Tooling | `agents-cli` (scaffold, eval, deploy), `uv` (Python deps), `pytest` (unit + integration) |
| Observability | Cloud Logging + OpenTelemetry → Cloud Trace |

## Highlights

- **3-tool agent** ([`app/tools.py`](app/tools.py)): `fetch_football_rss`, `write_digest_to_gcs`, `post_digest_to_discord`. Discord post strips `Source:` lines for a cleaner feed while GCS keeps the full sourced version. Auto-chunks at blank-line boundaries when over Discord's 2000-char per-message limit.
- **Tool-trajectory eval** ([`tests/eval/`](tests/eval/)): two cases via `agents-cli eval run` — trajectory order check + final-response shape rubric.
- **8 unit tests** ([`tests/unit/test_tools.py`](tests/unit/test_tools.py)) cover chunking, source-stripping, and a regression-blocking User-Agent assertion (Discord/Cloudflare returns 403 `error 1010` to the default Python-urllib UA).
- **Scaled to zero** between runs via a one-shot REST PATCH to `spec.deploymentSpec.minInstances=0`. Eats a ~30–60s cold start on the daily fire; saves $50–$300/mo of idle compute.
- **Reproducible** end-to-end — see [DESIGN_SPEC.md → Setup](DESIGN_SPEC.md#setup) for the full deployment recipe.

## Quick start

```bash
# Install the CLI + deps
uv tool install google-agents-cli
agents-cli install

# Local interactive testing (web UI)
agents-cli playground

# Run unit tests
uv run pytest tests/unit

# Run evaluations
agents-cli eval run

# Deploy to Vertex AI Agent Runtime
agents-cli deploy --project=<PROJECT_ID> --region=us-central1 \
  --update-env-vars "DIGEST_BUCKET=...,DISCORD_WEBHOOK_URL=...,GOOGLE_CLOUD_LOCATION=global"
```

See [DESIGN_SPEC.md](DESIGN_SPEC.md) for the design rationale, constraints, full deployment recipe, and operational gotchas (model 404s tied to `GOOGLE_CLOUD_LOCATION`, OAuth-vs-OIDC for Vertex AI, the Cloudflare UA block, the Windows `agents-cli` + `gcloud` subprocess quirk).

## Cost

With `min_instances=0` (scale-to-zero), idle baseline is ~$0/month. Per daily run: ~30–60s of 4 vCPU / 8 GB compute (cold start) + ~50K Gemini Flash tokens, totalling roughly **$0.30/month** of Vertex AI spend. GCS, Cloud Scheduler, and Cloud Logging are all within free tier at this volume.

Without scale-to-zero (default `min_instances=1`): ~$70–$355/month of idle compute, depending on whether the platform charges request-based or always-allocated CPU.

## Project layout

```
app/
  agent.py                  # LlmAgent definition + instruction
  tools.py                  # 3 tools: fetch_football_rss, write_digest_to_gcs, post_digest_to_discord
  config.py                 # AI Studio vs Vertex AI auto-detect; RSS feed list
  agent_runtime_app.py      # AgentEngineApp wrapper (telemetry + feedback)
  app_utils/                # telemetry + typing helpers
tests/
  unit/test_tools.py        # 8 tool-level tests (chunking, UA, source-stripping)
  integration/              # ADK Runner + AgentEngineApp integration tests
  eval/evalsets/*.json      # tool-trajectory + final-response eval cases
DESIGN_SPEC.md              # design + setup + operational notes
scheduler_body.json         # Cloud Scheduler request body (committed for reproducibility)
.env.example                # required env vars (DIGEST_BUCKET, DISCORD_WEBHOOK_URL)
pyproject.toml              # uv / hatch project config + agents-cli scaffold metadata
```
