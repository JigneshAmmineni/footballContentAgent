# DESIGN_SPEC.md — Football News Agent (MVP)

## Overview

Daily digest of the top 5 Big 5 Leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1) football stories, written to a GCS bucket as a timestamped markdown file each morning AND posted to a Discord channel via incoming webhook.

**Flow**: Cloud Scheduler fires an OAuth-authenticated HTTP call once per day directly to the Vertex AI Agent Runtime `streamQuery` endpoint → ADK agent fetches RSS from a fixed feed list → an LLM picks the top 5 stories and writes a short summary for each → the agent writes the digest to `gs://<DIGEST_BUCKET>/YYYY-MM-DD-HHMMSS.md` → the agent posts the same digest to a Discord channel via webhook.

Single ADK `LlmAgent` with three tools: `fetch_football_rss`, `write_digest_to_gcs`, and `post_digest_to_discord`. No curator/writer/critic split — PLAN.md's richer pipeline is deferred. No Pub/Sub indirection: Agent Runtime accepts Scheduler's signed HTTP call directly. GCS is the durable archive; Discord is the human-facing notification.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Agent framework | **Google Agent Development Kit (ADK)** | First-party, native tool calling, ships with the runtime |
| Runtime | **Vertex AI Agent Runtime** (Gemini Enterprise Agent Platform) | Serverless container hosting for ADK agents; built-in session storage; future-proof for chat / HITL frontends |
| Model | **Gemini Flash** via Vertex AI (Vertex when deployed; AI Studio fallback for local dev) | Cheapest current frontier for this volume; auto-detected in [`config.py`](app/config.py) |
| Trigger | **Cloud Scheduler** HTTP job, OAuth-authenticated | Native daily cron; no Pub/Sub plumbing needed |
| Archive | **Cloud Storage** (object writes via `google-cloud-storage`) | Durable, cheap, IAM-gated |
| Notification | **Discord** incoming webhook | Easiest possible delivery surface (no OAuth, no app registration) |
| Tooling | **`agents-cli`** (`uv tool install google-agents-cli`) | Scaffold, eval, deploy in one toolchain; replaces `gcloud run deploy` |
| Observability | Cloud Logging + OpenTelemetry / Cloud Trace | Built into ADK's `AgentEngineApp` setup |

## Example Use Case

**Trigger**: Cloud Scheduler job fires at 07:00 ET, POSTs an OAuth-signed HTTP request to the Agent Runtime `streamQuery` endpoint with a body containing the prompt `"generate today's football digest"`.

**Expected behavior**:
1. Agent calls `fetch_football_rss()` → returns ~50–150 recent items across feeds.
2. Agent reasons over items, filters to Big 5 Leagues relevance, picks exactly 5 most newsworthy, writes a 2–4 sentence summary each with source link.
3. Agent calls `write_digest_to_gcs(body=<markdown>)` → writes `gs://<DIGEST_BUCKET>/YYYY-MM-DD-HHMMSS.md` (UTC timestamp).
4. Agent calls `post_digest_to_discord(body=<same markdown>)` → POSTs the digest to the configured webhook (auto-chunked if it exceeds Discord's 2000-char per-message limit).
5. Agent emits a structured log with the 5 headlines + the GCS object URL for Cloud Logging.

**Expected markdown body**:
```
# Football Digest — YYYY-MM-DD

1. **[Headline 1]** — 2-4 sentence summary. [Source](url)
2. **[Headline 2]** — ...
...
```

## Tools Required

| Tool | Purpose | Auth |
|---|---|---|
| `fetch_football_rss` | Parse a fixed list of RSS feeds (BBC Sport, Guardian Football, ESPN FC, Goal.com, 90min) via `feedparser`. Returns `list[dict]` with title, link, summary, published, source. | None (public RSS). |
| `write_digest_to_gcs` | Upload markdown string to `gs://<DIGEST_BUCKET>/YYYY-MM-DD-HHMMSS.md` (UTC) via `google-cloud-storage`. Bucket name from env var `DIGEST_BUCKET`. Returns the `gs://` URL. | Runtime service account needs `roles/storage.objectCreator` on the bucket. Locally: Application Default Credentials. |
| `post_digest_to_discord` | POST the markdown digest to a Discord channel via incoming webhook using stdlib `urllib.request`. Webhook URL from env var `DISCORD_WEBHOOK_URL`. Strips `Source:` lines (the GCS archive retains them) for a cleaner channel feed. Splits content on blank-line boundaries when it exceeds Discord's 2000-char per-message limit. Sends a custom `User-Agent` header — Discord/Cloudflare blocks the default Python-urllib UA with error code 1010. | None beyond the webhook URL itself (incoming webhooks are unauthenticated by URL secret). |

**Gemini model**: latest via AI Studio (`GOOGLE_API_KEY`) for local runs; Vertex AI (via the Runtime SA's ADC) in deployed runs. Auto-detected in `config.py` per the `ambient-expense-agent` ADK sample. Concrete version confirmed at build time by listing available models.

## Constraints & Safety Rules

- **Bucket is a fixed env var** (`DIGEST_BUCKET`). Agent MUST NOT accept a bucket override from the Scheduler request payload or any tool input — prevents prompt-injection from a compromised feed redirecting output.
- **Discord webhook URL is a fixed env var** (`DISCORD_WEBHOOK_URL`), same reasoning: never accepted as a tool argument.
- **GCS write happens before Discord post.** GCS is the durable archive; if Discord fails, the day's digest is not lost.
- **No link shorteners, no tracking pixels** in the markdown body. Plain source URLs only.
- **Exactly 5 stories**, not more. If fewer than 5 Big-5-relevant items exist across all feeds (rare), write what's available and note the count.
- **No historical stats, no opinion, no transfer rumors from unsourced aggregators**. Summaries must trace to a real source URL in the RSS payload.
- **Deduplicate** by URL before selection (trivial dict dedup; no vector store yet).
- **Feeds failing to parse** are skipped silently with a WARNING log — one bad feed must not block the run.
- **Object names use UTC** (`YYYY-MM-DD-HHMMSS.md`) so ordering is stable regardless of Cloud Scheduler's invocation timezone.

## Success Criteria

1. `agents-cli run "generate today's football digest"` locally → agent fetches, picks 5, `write_digest_to_gcs` writes a valid markdown object, and `post_digest_to_discord` posts the same digest to the configured Discord channel.
2. `agents-cli eval run` → passes 2 seeded cases: (a) tool-trajectory check (`fetch_football_rss` → `write_digest_to_gcs` → `post_digest_to_discord` in that order); (b) final-response shape rubric (single `gs://...md` URL, no other prose).
3. After `agents-cli deploy`, a manual `streamQuery` call to the Agent Runtime endpoint produces a fresh object in the bucket within 60s AND a fresh Discord message in the configured channel.
4. Cloud Scheduler job fires daily at 07:00 ET and a fresh markdown file appears in the bucket and a fresh message appears in Discord.

## Setup

To reproduce this deployment in your own GCP project. All steps are reversible (or at least teardownable) until step 4, which spins up billable Vertex AI infra.

### Prerequisites

- A GCP project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login` + `gcloud auth application-default login`)
- `uv` for Python deps ([install](https://docs.astral.sh/uv/getting-started/installation/))
- A Discord channel + an incoming webhook URL (Channel → Edit → Integrations → Webhooks → New Webhook → Copy URL)

### 1. Local config

```bash
cp .env.example .env
# Edit .env to set:
#   DIGEST_BUCKET=<your-bucket-name>
#   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
#   GOOGLE_API_KEY=    # leave blank — local uses ADC against Vertex AI
```

### 2. Provision the GCS bucket and enable APIs

```bash
gcloud storage buckets create gs://<DIGEST_BUCKET> --location=us-central1

gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudresourcemanager.googleapis.com \
  logging.googleapis.com
```

### 3. Install + smoke test locally

```bash
uv tool install google-agents-cli
agents-cli install
uv run pytest tests/unit
agents-cli playground   # interactive web UI for local testing
```

### 4. Deploy to Vertex AI Agent Runtime

```bash
agents-cli deploy \
  --project=<PROJECT_ID> \
  --region=us-central1 \
  --update-env-vars "DIGEST_BUCKET=<DIGEST_BUCKET>,DISCORD_WEBHOOK_URL=<DISCORD_WEBHOOK_URL>,GOOGLE_CLOUD_LOCATION=global"
```

The CLI returns:
- A **Runtime ID** (e.g. `projects/.../reasoningEngines/<RUNTIME_ID>`)
- A **Runtime SA email** (e.g. `service-<PROJECT_NUMBER>@gcp-sa-aiplatform-re.iam.gserviceaccount.com`)

`GOOGLE_CLOUD_LOCATION=global` is **required** — without it the runtime defaults to its own region (e.g. `us-central1`) where `gemini-flash-latest` is not served, returning a model 404.

### 5. Grant the Runtime SA write permission to the bucket

```bash
gcloud storage buckets add-iam-policy-binding gs://<DIGEST_BUCKET> \
  --member="serviceAccount:<RUNTIME_SA_EMAIL>" \
  --role="roles/storage.objectCreator"
```

### 6. Create the Cloud Scheduler job

```bash
# Dedicated SA for the Scheduler caller
gcloud iam service-accounts create football-digest-scheduler \
  --display-name="Football Digest Scheduler"

gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:football-digest-scheduler@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# The cron job — body is in scheduler_body.json (committed to repo)
gcloud scheduler jobs create http football-digest-daily \
  --location=us-central1 \
  --schedule="0 7 * * *" \
  --time-zone="America/New_York" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1beta1/projects/<PROJECT_NUMBER>/locations/us-central1/reasoningEngines/<RUNTIME_ID>:streamQuery" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body-from-file=scheduler_body.json \
  --oauth-service-account-email="football-digest-scheduler@<PROJECT_ID>.iam.gserviceaccount.com" \
  --attempt-deadline=15m
```

Vertex AI's `aiplatform.googleapis.com` accepts **OAuth access tokens, not OIDC ID tokens** — so use `--oauth-service-account-email`, not `--oidc-service-account-email`. (The original spec's reference to OIDC was incorrect; corrected during deploy.)

### 7. Smoke-test end-to-end in prod

```bash
gcloud scheduler jobs run football-digest-daily --location=us-central1
# Wait ~10s, then:
gcloud storage ls gs://<DIGEST_BUCKET>/  # should show a fresh YYYY-MM-DD-HHMMSS.md
# And check Discord — fresh message should have arrived
```

### 8. (Recommended) Scale to zero between runs

The `agents-cli deploy` default sets `min_instances=1`, which keeps a 4 vCPU / 8 GB container alive 24/7. For a once-a-day cron that's wasteful. Patch to scale-to-zero:

```bash
TOKEN=$(gcloud auth print-access-token)
curl -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/<PROJECT_ID>/locations/us-central1/reasoningEngines/<RUNTIME_ID>?updateMask=spec.deploymentSpec.minInstances" \
  -d '{"spec":{"deploymentSpec":{"minInstances":0}}}'
```

Tradeoff: each cron fire now eats a 30–60s cold start (heavy Python imports). Cloud Scheduler's `--attempt-deadline=15m` covers this comfortably.

## Reference Samples Applied

- **`ambient-expense-agent`** — initially used as the architectural reference. Applied: AI Studio/Vertex auto-detect in `config.py`, structured JSON logging pattern. Dropped after the target switch: the sample's Pub/Sub push shape and FastAPI wrapper are Cloud-Run-only — Agent Runtime gets the same daily trigger via a simpler Scheduler-HTTP call.
- If a frontend / HITL surface is ever added, reference `adk-ae-oauth` (user-facing agent on Agent Runtime with OAuth) and `genmedia-for-commerce` (React + Agent Runtime) as the shape.

## Out of Scope

Curator/writer/critic 3-agent pipeline, match results section, player updates section, stat insights, stat comparisons, ContentBundle Pydantic schema, PlatformAdapter ABC, Instagram publisher, image generation, novelty check against prior bundles.

## Operational notes (lessons learned during deploy)

- **Re-deploys**: each `agents-cli deploy --update-env-vars` takes 5–10 min and replaces the runtime image. Pass *all* required env vars on every deploy — `--update-env-vars` appears to merge, but to be safe re-pass `DIGEST_BUCKET`, `DISCORD_WEBHOOK_URL`, and `GOOGLE_CLOUD_LOCATION` together.
- **`GOOGLE_CLOUD_LOCATION=global`** is mandatory for `gemini-flash-latest` on Vertex AI — without it the runtime hits the model at its own region (e.g. `us-central1`) where the model isn't published and returns 404. Locally, `app/config.py` sets this via `os.environ.setdefault`, but on the deployed runtime the platform pre-sets `GOOGLE_CLOUD_LOCATION=us-central1`, so `setdefault` is a no-op. Override explicitly in `--update-env-vars`.
- **Discord webhook UA**: Discord/Cloudflare returns `403 error code 1010` for the default Python-urllib User-Agent. The tool sends a custom `User-Agent` header; a unit test asserts this so the regression can't return silently.
- **OAuth, not OIDC, for Vertex AI**: `aiplatform.googleapis.com` is a Google API and accepts OAuth access tokens. `oidc-service-account-email` (which produces ID tokens, used for Cloud Run / custom HTTPS services) won't authenticate. Use `--oauth-service-account-email` on the Scheduler job.
- **Windows + `agents-cli` + `gcloud`**: on Windows, `agents-cli deploy` calls `subprocess.run(["gcloud", ...])` which fails with `FileNotFoundError` because Python's CreateProcess doesn't honor `PATHEXT` for `.cmd` wrappers. Workaround: pass `--project` and `--region` explicitly to skip the gcloud project lookup.
- **Cost optimization**: `min_instances=1` is the agents-cli default and keeps a 4 vCPU / 8 GB container provisioned 24/7. For cron-triggered agents, PATCH to `min_instances=0` post-deploy (see Setup §8). Memory is billed continuously while the container is provisioned; CPU is mostly billed during active requests (Linux event loops idle at near-zero CPU). Use Console → Billing → Reports, grouped by SKU, to see the breakdown — Vertex AI vCPU, Vertex AI memory, Gemini input/output tokens, GCS storage, and Cloud Scheduler are all separate SKUs.
