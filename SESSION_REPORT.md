# Session Report — scoring pipeline validation (2026-04-23)

## TL;DR
Pipeline is healthy end-to-end for scoring + recency + telemetry, but **max
`scored_jobs.score` = 60**. Writer requires `>= 70`, so no `.docx` pairs were
produced. Details + remediation in `BLOCK_3_5_DIAGNOSTIC.md`.

## What was touched
* **`src/utils/llm_chain.py`** — when every provider is cooling, wait up to
  `MAX_WAIT_FOR_COOLDOWN_S` (100s) for the earliest mark to expire before
  giving up, instead of instantly raising "All providers exhausted". Without
  this the 20-job batch burned through every pending job in ~30 seconds.
* **`src/utils/providers/gemini_provider.py`** — set
  `ThinkingConfig(thinking_budget=0)`. Gemini 2.5-flash was spending the
  `max_output_tokens` budget on invisible thinking and returning
  truncated JSON (`{\\n "verdict": "` with nothing after).
* **`src/scoring/scorer.py`** — `max_tokens` 700→900 (fits JSON with safety
  margin now that thinking is off). Earlier session already landed
  `SCORING_CONCURRENCY=1` + `BULK_SCORE_MIN_INTERVAL_S=6.5` + recency delta.
* **`src/main.py`** — `BULK_SCORE_BATCH_CAP` 50→20 to fit in free-tier budgets.
* **`tests/test_llm_chain.py`** — monkeypatch `MAX_WAIT_FOR_COOLDOWN_S=0`
  in the autouse fixture so `test_chain_raises_when_all_exhausted` doesn't
  actually sleep 90s.
* **`scripts/validate_outputs.py`** — mechanical validator (size, candidate
  name, company slug, bullet count, banned-cliche scan) for future `.docx`
  runs. Unused this session because the writer stage never produced output.
* **`runs`** table — marked the four test-cycle runs that I killed
  mid-execution as `status='failed'` so the table isn't poisoned by
  never-finished rows.

## What I validated
* `scored_jobs.model` is now stamped with the real backend
  (`groq:llama-3.1-8b-instant`, `groq:llama-3.3-70b-versatile`). ✅
* `age_days` is populated on 100% of new rows; `recency_bonus` in {0, 3}
  matches the tier table. ✅
* `llm_calls` telemetry shows 32 successes, 408 failures in the last 2h —
  failures dominated by provider 429s, not schema/auth issues. ✅
* Chain fallback + wait-for-cooldown exercised live (saw 70b→8b handoff and
  `llm_chain.waiting_for_cooldown` events producing real Groq 8b successes
  after the wait). ✅
* `pytest` 102/102 green after each change. ✅

## Why the run didn't produce `.docx`
The scorer's rubric + the current batch of postings put every new row in the
"stretch" band (0–60). Recency delta tops out at +3. Writer gate is 70.
Numbers simply don't stack. See `BLOCK_3_5_DIAGNOSTIC.md` §§2–4 for the
rubric analysis and three recommended paths (lower threshold, loosen
rubric, or widen recency weighting). Recommendation: for a one-shot demo
set `MIN_SCORE_TO_NOTIFY=60`; separately loosen `data/prompts/scorer.txt`
for the next pull.

## Still-open provider realities
* Gemini free: 20 RPM cap — easily burned in testing.
* Groq llama-3.3-70b: 100k TPD exhausted this session; resets at UTC 00:00.
* Groq llama-3.1-8b: 6k TPM sliding → ~1.5 req/min at our ~4k-token prompt.
* OpenRouter llama-3.3-70b-instruct:free: Venice upstream returning 429s.

The chain's new wait-for-cooldown keeps throughput around "best available
provider" rather than collapsing, but is not a replacement for a paid key.

## Suggested next moves (in order)
1. Run `MIN_SCORE_TO_NOTIFY=60 .venv/Scripts/python.exe -m src.main score-all`
   once Groq 70b TPD resets — should produce 1+ `.docx` pair and let you
   exercise `scripts/validate_outputs.py`.
2. Tune `data/prompts/scorer.txt` so the rubric's "match" band starts at 65
   and the three priority tracks (`analytics_engineer`, `ops_data_analyst`,
   `automation_dev`) get an explicit +5 boost.
3. Re-run and drop the threshold override.
