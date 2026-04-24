# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

The Windows `py` / `python` on `PATH` lacks the project deps. Use the project venv:

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
.venv/Scripts/python.exe -m src.main <mode>
```

Python 3.12 locally, 3.11 in CI. `asyncio_mode = auto` in `pytest.ini` — async tests don't need `@pytest.mark.asyncio`.

## Common commands

```bash
.venv/Scripts/python.exe -m pytest tests/ -v                      # unit suite, no network
.venv/Scripts/python.exe -m pytest tests/test_recency.py -v       # one file
.venv/Scripts/python.exe -m pytest tests/test_scoring.py::test_prefilter_accepts_junior  # one test
.venv/Scripts/python.exe -m pytest -m integration                 # hits real APIs (opt-in)

.venv/Scripts/python.exe -m src.main                   # full cycle: fetch → score → write
.venv/Scripts/python.exe -m src.main fetch             # fetch + persist only
.venv/Scripts/python.exe -m src.main score             # score unscored jobs from last 48h
.venv/Scripts/python.exe -m src.main rescore           # re-evaluate prefilter-skipped rows
.venv/Scripts/python.exe -m src.main providers         # print LLM chain + in-process status (no network)
```

The `integration` marker is defined in `pytest.ini` and excluded from the default run. Every other test is pure Python / monkeypatched.

`config/settings.py` validates env vars **at import time** — any module that imports `config.settings` will raise `RuntimeError` if `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GROQ_API_KEY`, `TELEGRAM_BOT_TOKEN`, or `TELEGRAM_CHAT_ID` is missing. Tests mock `src.db.client` or patch providers to avoid needing real credentials.

## Architecture

### Pipeline stages

`src/main.py` composes four stages into the cycle modes listed above:

1. **Fetch** — `src/sources/fetcher.py` fans out to 8 adapters in `src/sources/*.py` (RemoteOK, WeWorkRemotely, Remotive, WorkingNomads, Jobicy, Himalayas, LinkedIn RSS, Indeed RSS). Each adapter implements `BaseJobSource` from `src/sources/base.py` and emits `Job` models.
2. **Persist + dedupe** — `src/db/client.persist_new_jobs` batches one SELECT (existence check on `(source, external_id)`) then one INSERT of the remainder.
3. **Score** — `src/scoring/scorer.score_jobs`:
   - runs heuristic `prefilter_job` (pure Python, no LLM) — drops obvious non-matches into `skipped_jobs` with `skip_stage='prefilter'`.
   - fans surviving jobs through the LLM with `Semaphore(5)`.
   - applies `compute_recency_delta(job.posted_at)` to the LLM's raw score, clamps to `[0, 100]`, persists `recency_bonus` + `age_days`.
   - `skip` verdicts and `blocked_citizen_only` auth statuses also land in `skipped_jobs` (with distinct `skip_stage` tags) on top of their `scored_jobs` row.
4. **Write** — `src/writing/writer.write_all_matching` filters on `score >= MIN_SCORE_TO_NOTIFY` & verdict ≠ `skip` & auth ≠ `blocked_citizen_only`, generates a tailored `.docx` resume + cover letter per qualifying job (capped at `Semaphore(3)`), and upserts `applications` rows with `status='suggested'`.

Block 4 (Telegram notify in `src/notify/`) is not yet wired into `main.py`.

### LLM fallback chain

Core of Block 3.5. All LLM calls go through `src/utils/llm.py`'s `chat()` / `chat_with_meta()`, which lazily builds a process-wide `LLMFallbackChain` via `config/llm_config.build_default_chain()`.

- **Provider order** (set by `build_default_chain` from env-var presence): Gemini `2.5-flash` → Groq `llama-3.3-70b-versatile` / `llama-3.1-8b-instant` / `gemma2-9b-it` → OpenRouter `gemini-2.0-flash-exp:free` / `llama-3.3-70b-instruct:free`. Missing keys drop their provider; missing all providers raises `RuntimeError`.
- **Exhaustion tracking** (`src/utils/llm_chain.py`): a `QuotaExceededError` adds the provider's `"<name>:<model>"` key to `chain.exhausted` for the rest of the process; `ProviderError` falls back without marking. Multiple Groq models share `name="groq"` — the `name:model` key keeps them distinct.
- **Telemetry**: every provider attempt (success or failure) writes a row to `llm_calls` via `src/utils/llm_telemetry.record_call`. `run_id` and `stage` come from `contextvars` set by `llm_context(run_id=..., stage=...)` wrappers in `src/main._score_and_write` and `src/writing/writer.write_materials_for_job`. Telemetry failures are logged and swallowed — they never break the scoring path.

### Database layer

`src/db/client.py` is the single entry point for all Supabase I/O — models in / models out (Pydantic v2 in `src/models/job.py`). Schema lives in `src/db/schema.sql`; incremental migrations in `src/db/migrations/NNN_*.sql` (apply via Supabase SQL editor or Supabase MCP). Current migrations: `002_skipped_jobs`, `003_llm_calls`, `004_recency`.

Tables: `jobs` · `scored_jobs` · `applications` · `skipped_jobs` · `runs` · `llm_calls`.

### Prompt templating

`data/prompts/{scorer,resume_tailor,cover_letter}.txt` use `{placeholder}` markers filled via **`str.replace()`** (not `.format()`) — JSON curly braces inside the few-shot examples stay un-escaped.

### Resume tailor anti-hallucination guardrail

`src/writing/resume_tailor.py` re-validates every LLM-returned highlight against `data/profile.json` by token-overlap (70% threshold). If the LLM invents or drifts, the tailor falls back to a deterministic keyword-ranked pick from `profile.experience[].highlights` so the generated `.docx` never claims anything the profile doesn't support.

### Output files

Generated `.docx` files land in `data/outputs/{run_id_short}/` as `{company}_{title}_{resume|cover}.docx`. Builders in `src/writing/docx_builder.py` write to `.docx.tmp` then `os.replace` to the final name so a crash mid-render leaves no half-file behind. The folder is gitignored.

## Gotchas

- **Supabase client** is pinned to `2.28.3` — older `2.10.x` hardcodes a JWT regex that rejects newer `sb_secret_*` keys.
- **Jobicy** adapter: do **not** add an `industry=` query param — the endpoint 400s on it. The adapter retries with zero params on an unexpected 400 for resilience.
- **Prefilter is strict (junior-only).** Hard-rejects any title containing senior / sr / lead / manager / mid-level / intermediate, plus the original staff / principal / director / VP / head-of / chief, plus 7+ years in title, domain blocklist (nursing, sales, customer success, business development, medical coding, performance marketing, crypto trading, L&D, etc.), stack mismatches (iOS / Unity / firmware / mechanical / hardware), and `allows_canada=False`. The `seniority_hint` soft-flag path from Block 3.5 is gone — `_HARD_TITLE_RE` uses word boundaries so "seniority" / "leadership" / "managerial" are safe false-positive cases. When **loosening** prefilter rules that already dropped rows, run `python -m src.main rescore` to re-evaluate the `skipped_jobs` backlog.
- **`ScoredJob.model`** column is stamped with `f"{resp.provider}:{resp.model}"` from the actual backend that answered — not the first provider in the chain — so the scorer uses `chat_with_meta()` to get that metadata back.
