# Job Hunter Agent

An automated job-hunting pipeline that scans remote-Canada data jobs three times a day on GitHub Actions, scores each posting against Davi Almeida's profile with a multi-provider LLM fallback chain (Gemini → Groq → OpenRouter), and sends the top matches to Telegram along with a tailored résumé (`.docx`) and cover letter. Every run is persisted to Supabase so nothing is scored twice.

## Architecture

```
  Job Sources                       Pipeline                           Outputs
  ─────────────                     ────────                           ───────
  RemoteOK API   ─┐
  WeWorkRemotely  │
  Remotive API   ─┤
  WorkingNomads   │                                                    Telegram
  Jobicy API      ├─▶ fetch ─▶ dedupe ─▶ score ─▶ tailor ─▶ notify ─▶  (bot + .docx)
  Himalayas JSON  │            (DB)     (LLM      (python-   │
  LinkedIn RSS   ─┤                     chain)    docx)      └──▶ generated files
  Indeed RSS     ─┘                                                    (data/outputs/)

  LLM chain: Gemini 2.5-flash → Groq (3 models) → OpenRouter (2 free models)
             Per-run exhaustion tracking; telemetry row per call in `llm_calls`.

  State:  Supabase (jobs · scored_jobs · applications · skipped_jobs · runs · llm_calls)
  Cron:   GitHub Actions (3× per day)
```

## Repository layout

```
job-hunter-agent/
├── config/                    env loading, source configs
├── data/
│   ├── profile.json           structured master profile (scorer + tailor input)
│   ├── master_resume.md       plain-text résumé (fill in yourself)
│   └── prompts/               scorer / resume_tailor / cover_letter templates
├── src/
│   ├── main.py                entry point (Block 4)
│   ├── db/                    Supabase client + schema.sql
│   ├── models/                pydantic v2 models
│   ├── sources/               job source adapters (Block 2)
│   ├── scoring/               LLM scoring (Block 3)
│   ├── writing/               résumé + cover-letter generation (Block 3)
│   ├── notify/                Telegram notifier (Block 4)
│   └── utils/                 structlog + Groq wrapper
├── tests/                     smoke tests
└── .github/workflows/run.yml  cron (Block 4 flips the pipeline step on)
```

## Setup

### 1. Prerequisites
- Python 3.11 (3.12 also works locally).
- A Supabase project (free tier).
- A Telegram bot (created via `@BotFather`).
- **At least one LLM API key**. The pipeline uses a fallback chain — set as
  many as you have and the chain picks up whichever is healthy:
  - `GEMINI_API_KEY` (Google Gemini — generous free tier, first in the chain)
  - `GROQ_API_KEY` (Groq — fast, but a ~100k token/day cap)
  - `OPENROUTER_API_KEY` (OpenRouter — final fallback to free-tier models)

### 2. Install
```bash
git clone <your-fork>
cd job-hunter-agent

python -m venv .venv
# Windows (git-bash or similar):
source .venv/Scripts/activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Fill SUPABASE_URL, SUPABASE_SERVICE_KEY, GROQ_API_KEY,
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
```

### 4. Initialise the database
Open `src/db/schema.sql`, paste into the Supabase **SQL editor**, and **Run**. The script is idempotent and safe to re-run.

**Block 3 adds one extra table** — once the base schema is in place, also run `src/db/migrations/002_skipped_jobs.sql` in the SQL editor. It creates `skipped_jobs` (the table that records why a posting was dropped before or after scoring). Doing this *before* running Block 3 is required; the scorer writes to it on every prefilter or LLM `skip` verdict.

**Block 3.5 adds a second migration** — run `src/db/migrations/003_llm_calls.sql` next. It creates `llm_calls`, a one-row-per-LLM-call telemetry table (provider, model, tokens in/out, latency, success/error, the `run_id` it belonged to). The chain writes to this table on every attempt — even failed ones — so you can see how fast you're burning through each provider's quota.

### 5. Drop in your master résumé
Replace the placeholder in `data/master_resume.md` with the full content of `Resume_Davi_v2.md`.

### 6. Verify
```bash
pytest tests/ -v
```
Every test should pass (the suite does not make network calls).

## How to get each credential

### Supabase
1. Create a project at <https://supabase.com/dashboard>.
2. **Settings → API**:
   - `SUPABASE_URL` = the Project URL.
   - `SUPABASE_SERVICE_KEY` = the `service_role` secret (not the `anon` key). Treat it like a password — it bypasses RLS.
3. **SQL editor** → paste `src/db/schema.sql` → **Run**.

### LLM providers (fallback chain — set one or more)

The chain tries providers in this order, marks each as exhausted on a quota
error, and moves on. The first one with capacity answers. Every call writes
a row to `llm_calls` for telemetry.

| # | Provider   | Models (in order)                                       | Free tier notes |
|---|------------|---------------------------------------------------------|-----------------|
| 1 | Gemini     | `gemini-2.5-flash`                                      | Generous RPM + daily tokens |
| 2 | Groq       | `llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → `gemma2-9b-it` | ~100k TPD shared across models |
| 3 | OpenRouter | `google/gemini-2.0-flash-exp:free` → `meta-llama/llama-3.3-70b-instruct:free` | Rate-limited but free |

**Google Gemini**
1. Go to <https://aistudio.google.com/apikey>.
2. **Create API key** → copy into `GEMINI_API_KEY`.

**Groq**
1. Sign up at <https://console.groq.com>.
2. **API Keys → Create API Key** → copy into `GROQ_API_KEY`.
3. `GROQ_MODEL` is legacy; the chain now iterates the three Groq models
   above independently.

**OpenRouter**
1. Create an account at <https://openrouter.ai>.
2. **Keys → Create Key** → copy into `OPENROUTER_API_KEY`.

### Telegram
1. Message `@BotFather` on Telegram → `/newbot` → pick a name → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send *any* message to your new bot (this creates the chat).
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. Look for `"chat":{"id": <n> ...}` — that integer is `TELEGRAM_CHAT_ID`.

## Local development

Blocks 1–3 are wired up. `src/main.py` now runs one fetch → persist → score → write cycle; Telegram delivery lands in Block 4.

```bash
pytest tests/ -v                                # unit tests only, no network
pytest tests/test_sources.py -v -m integration  # hits RemoteOK / WWR / Remotive / etc.
python -m src.main                              # full cycle: fetch → score → write
python -m src.main fetch                        # Block 2 behaviour only (no LLM calls)
python -m src.main score                        # score any unscored jobs from the last 48h
python -m src.main rescore                      # re-evaluate prefilter-skipped rows (Block 3.5)
# --rescore also works as a flag alias for the same mode.
```

`rescore` is the knob you pull after tightening or relaxing the prefilter
rules (for example: Block 3.5 stopped hard-rejecting "senior" titles, so
rows dropped under Block 3 can now come back in). It loads every
`skipped_jobs` row with `skip_stage = 'prefilter'`, runs the current
prefilter against each, deletes the skip row for anything newly accepted,
and fans the survivors through `score_jobs` → `write_all_matching`.

Expected `python -m src.main` behaviour:
- fetches new postings and inserts them into `jobs`
- runs the heuristic prefilter (cheap Python, no LLM) to drop obvious non-fits
- calls Groq for the survivors, writes rows to `scored_jobs`
- generates a tailored `.docx` resume + cover letter for every job scoring `>= MIN_SCORE_TO_NOTIFY` (default 70) whose verdict isn't `skip` and whose auth isn't blocked
- writes an `applications` row per generated pair with `status = "suggested"`
- writes every dropped job into `skipped_jobs` with a `skip_stage` tag (`prefilter` / `llm_verdict_skip` / `auth_blocked`)

Outputs land in `data/outputs/{run_id_short}/` as `{company}_{title}_resume.docx` and `{company}_{title}_cover.docx`. The folder is gitignored — delete old runs freely.

### Manually testing the writer on one job

```python
import asyncio, json
from pathlib import Path
from src.db.client import load_scored_jobs_by_ids
from src.writing.writer import write_materials_for_job
from src.writing.paths import make_run_output_dir

async def one(scored_job_id: str):
    scored = load_scored_jobs_by_ids([scored_job_id])[0]
    profile = json.loads(Path("data/profile.json").read_text(encoding="utf-8"))
    out_dir = make_run_output_dir("manual")
    paths = await write_materials_for_job(scored, profile, out_dir)
    print(paths)

asyncio.run(one("<your-scored-job-uuid>"))
```

## Build log

- [x] **Block 1 — Foundation.**
    Package tree, env loading, Supabase schema + client, pydantic models,
    Groq wrapper with retry, structlog (JSON in CI / pretty locally), prompt
    templates, smoke-test suite, CI skeleton.
- [x] **Block 2 — Sources.**
    Adapters for RemoteOK, We Work Remotely, Remotive, Working Nomads,
    Jobicy, Himalayas. Parallel async fetch, in-batch + DB dedupe on
    `(source, external_id)`, and insert into `jobs`. `src/main.py` runs
    one end-to-end fetch cycle.
- [x] **Block 3 — Score & write.**
    Heuristic prefilter (no LLM) drops obvious non-matches into `skipped_jobs`.
    Groq scorer fans out with `Semaphore(5)` and strict JSON parsing, writing
    to `scored_jobs`. Resume tailoring (`SelectedHighlight`s fuzzy-checked
    against the profile to kill hallucinations) + cover letter (EN/FR
    auto-detected, word budget enforced) generate `.docx` files via
    `python-docx`, atomically written under `data/outputs/{run_id}/`. Each
    qualifying job gets an `applications` row with `status = "suggested"`.
- [x] **Block 3.5 — Resilience & breadth.**
    Three real-world issues fixed after the first end-to-end run.
    - **Multi-provider LLM fallback chain.** `src/utils/llm_chain.py` tries
      Gemini → Groq → OpenRouter in order, marks providers exhausted on
      quota errors, falls back without marking on transient errors. One
      provider going down no longer kills the run.
    - **Smarter prefilter.** "Senior" in the title no longer hard-rejects —
      the LLM now reads the description and decides based on actual
      years-of-experience language. Hard rejects are reserved for
      staff/principal/director, `Lead {engineer,developer,architect,scientist}`,
      7+ years in title, domain blocklist, and stack mismatch.
    - **Wider source pool.** Jobicy fixed (dropped the bad `industry=`
      param + added a no-params fallback on 400). Added LinkedIn and
      Indeed RSS adapters that fan out across five queries each.
    - **Re-run safety.** `python -m src.main rescore` re-evaluates every
      `skipped_jobs` row that was dropped by the prefilter against the
      current rules, deletes the row for newly accepted jobs, and sends
      them through scoring.
    - **Provider telemetry.** New `llm_calls` table records one row per
      provider call (tokens in/out, latency, success/error, run_id).
- [x] **Block 4 — Orchestrate & notify.**
    `src/notify/telegram.py` sends one `sendMessage` (HTML summary of score,
    verdict, track, why-match, watch-out, link) plus `sendDocument` for the
    resume and cover letter per match. `applications.notified_at` tracks
    successful sends so the same posting is never re-delivered. Every
    pipeline mode (`full` / `score` / `score-all` / `rescore`) now ends with
    a notify stage; a new `python -m src.main notify` CLI replays any
    unnotified backlog. `DRY_RUN=true` short-circuits the HTTP calls. The
    workflow's `Run pipeline` step is no longer gated behind `if: false`.
- [x] **Block 4.5 — Junior-only tightening.**
    Prefilter promoted senior / sr / lead / manager / mid-level / intermediate
    to hard-reject, and the domain blocklist was extended with business
    development, medical coding, performance marketing, crypto trading, and
    learning-and-development. LinkedIn and Indeed RSS adapters disabled
    (neither ever produced a row in this project's lifetime — 403 Forbidden
    and empty feeds respectively). Profile `data/profile.json` refreshed
    against the updated `master_resume.md` (new UAP experience, SharePoint
    redesign highlight, FNAT education, corrected language proficiencies,
    refreshed project list).

## Decisions & notes

- **`profile.json`**
    - `work_authorization.pr_status` is `"pr_approved_copr_pending"` — the provided sample said `"ITA_received_express_entry"`, but that contradicts the rest of the JSON (notably `resume_statement`) and the context statement ("PR approved, COPR pending"), so it was aligned. Edit if your status changes.
    - All other fields (email, phone, LinkedIn, GitHub, website) are copied verbatim from the spec. Update if they drift.
- **`.env.example`** lists `GMAIL_*` placeholders reserved for a future block — they are not read by current code.
- **Python versions** — the CI job pins 3.11; 3.12 works locally and is what the dev machine uses.
- **Prompt templating** — prompts use `{placeholder}` markers and are filled in via `str.replace()` (not `str.format()`) so the JSON curly braces in the few-shot examples don't need to be doubled.
- **Supabase Python client** — pinned to `2.28.3`. The older `2.10.x` line has a hardcoded JWT-format regex for API keys and rejects the newer `sb_secret_*` format that Supabase now issues.
- **Jobicy** — Block 3.5 fixes the `400 Bad Request` that Block 2/3 hit. The `industry=` query param was the culprit; removing it and filtering by `is_data_relevant()` on the client side restored the feed. The adapter also retries with zero params on an unexpected 400 to stay resilient against further endpoint changes.
- **LLM fallback chain** — Block 3.5 replaces the single-provider Groq setup with `LLMFallbackChain`. Provider order is Gemini → Groq (3 models) → OpenRouter (2 free models). A `QuotaExceededError` marks that provider exhausted for the rest of the run; a transient `ProviderError` does *not*, so the same provider is still tried on the next call. Scoring caps concurrency at `Semaphore(5)`, writing at `Semaphore(3)`.
- **LLM telemetry** — `llm_calls` stores one row per provider call via `src.utils.llm_telemetry.record_call` (invoked from the chain). `run_id` and `stage` come from contextvars set by `src.main._score_and_write` → `llm_context(run_id=run_id)` wrapping scorer + writer blocks. Telemetry failures never break the scoring path — they're logged and swallowed.
- **Anti-hallucination guardrail** — the resume tailor re-validates every returned highlight against the profile by token overlap (70% threshold). When the LLM drifts or invents, we fall back to a deterministic keyword-ranked pick from `profile.experience[].highlights` so the `.docx` never claims anything the profile doesn't support.
- **Atomic .docx writes** — builders save to `.docx.tmp` and then `os.replace` to the final name. A crash mid-render leaves no half-written file the notifier might later attach.
