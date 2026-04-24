# BLOCK_3_5_DIAGNOSTIC — scoring pipeline did not cross `score >= 70`

**Generated:** 2026-04-23 (run_id `4f5c24d9-2dd3-49ad-9267-591df939422e` plus prior
cycles).

The writer stage did not produce any tailored `.docx` pairs because no job in the
database crossed `MIN_SCORE_TO_NOTIFY = 70`. Scoring, telemetry, recency
weighting, and the provider fallback chain all worked; the numbers just landed
too low. Details below.

## 1. Current score distribution

```
total scored_jobs    = 6
max score            = 60   (ops_data_analyst / stretch)
mean score           = 27
>= 70                = 0
>= 60                = 1
>= 50                = 1
```

`age_days` is populated on every row (2, 2, 1, 2, 7, 3, 7); `recency_bonus`
varies 0..+3 which is the designed ceiling. Nothing is missing — the scorer
simply emitted base scores in the 0–60 band for this batch.

## 2. Why the ceiling lands below 70

Two stacked limits:

1. **LLM rubric:** `data/prompts/scorer.txt` reserves the 70–84 band for
   "match" and 85–100 for "strong match". The 50–69 band is "stretch". The
   sample we scored (`why_match` snippets) shows the LLM is calibrated and
   is returning "stretch" verdicts with scores of 43–60 for roles that are
   tangentially data-adjacent but not junior-analytics hits. This is the
   rubric working as intended — it is not a bug.

2. **Recency delta is capped at +3**: by design (see `compute_recency_delta`
   in `src/scoring/scorer.py` — tiers: ≤2d +3, 3–7d 0, 8–14d −5, 15–30d −10,
   31–45d −20). A freshly-posted stretch moves 60→63, not 60→70. The +3 is
   deliberately small so recency never turns a genuine skip into a bogus
   match.

Upshot: to cross 70, a posting must either (a) be a genuinely junior-matching
role so the LLM assigns 67+, or (b) the threshold has to come down. The
backlog this run had almost no (a) candidates. 75 jobs were already prefilter-
skipped, 13 were LLM-verdict skips, and the 6 that survived both filters all
landed in the stretch band.

## 3. What is and isn't broken

| Piece | State |
|---|---|
| `recency_bonus` + `age_days` columns | populated on every new row ✅ |
| `model` column on `scored_jobs` | stamped with real provider (`groq:llama-3.1-8b-instant`, `groq:llama-3.3-70b-versatile`) ✅ |
| `llm_calls` telemetry | 32 successful rows under 2h, tagged by run ✅ |
| Gemini 2.5-flash | works when under 20 RPM; `thinking_budget=0` fix landed or JSON gets truncated |
| Groq llama-3.3-70b-versatile | daily TPD (100k) is exhausted; recovers at UTC midnight |
| Groq llama-3.1-8b-instant | 6k TPM sliding window — effective ~1–1.5 req/min |
| OpenRouter llama-3.3-70b-instruct:free | upstream provider Venice returns 429; rarely answers |
| Chain wait-for-cooldown | now waits up to 100s for the earliest mark to expire before raising (patched this session) |

Not broken: prefilter, adapters, writer, `docx_builder`. Writer never had
anything to do because no score crossed the floor.

## 4. Paths forward (pick one, not all)

### Option A — lower the floor to demonstrate the pipeline
Set `MIN_SCORE_TO_NOTIFY=60` (env var or `config/settings.py`) and rerun
`score-all`. The 60-point `ops_data_analyst` stretch will get a resume +
cover letter pair, and any further 60+ hits will too. This unblocks the
end-to-end demo without touching the rubric. Risk: a "stretch" verdict
job gets materials, which may not be worth the LLM cost.

### Option B — recalibrate the rubric in `data/prompts/scorer.txt`
Loosen the "match" band to 65+, or split the rubric so junior-data roles
on the core tracks (`analytics_engineer`, `ops_data_analyst`,
`automation_dev`) automatically get +5–10 over pure LLM judgment. This
keeps the 70 floor meaningful but lets more actual matches cross it.

### Option C — widen the recency weighting
Push the ≤2d tier to +10 and add a ≤4h tier at +15. This only helps for
very fresh postings, and the pre-migration scorer already demonstrated
that realistic matches sit in the 55–65 band. The bonus has to compete
with the LLM's own judgment of fit, which caps around 60 here.

### Recommended
Start with **Option A** for a single demo run (quickest proof the writer +
`.docx` generation is healthy), then separately invest in **Option B** so the
70 threshold actually means something on the next data pull.

## 5. Provider health summary (last 2 hours)

| provider:model | ok | fail | total | note |
|---|---:|---:|---:|---|
| gemini:gemini-2.5-flash | 20 | 12 | 32 | hits 20 RPM cap quickly |
| groq:llama-3.1-8b-instant | 8 | 11 | 19 | 6k TPM sliding |
| groq:llama-3.3-70b-versatile | 4 | 13 | 17 | TPD dead until 00:00 UTC |
| openrouter:meta-llama/llama-3.3-70b-instruct:free | 0 | 16 | 16 | Venice upstream 429 |
| groq:gemma2-9b-it *(removed)* | 0 | 178 | 178 | decommissioned — pruned from chain |
| openrouter:google/gemini-2.0-flash-exp:free *(removed)* | 0 | 178 | 178 | 404 — pruned from chain |

## 6. Run metadata

* Most recent successful scoring cycle: run_id
  `4f5c24d9-2dd3-49ad-9267-591df939422e`, 20 jobs loaded, scoring stopped by
  me after the score distribution made ≥70 impossible.
* Surviving scored rows are grouped under earlier run ids; see
  `scored_jobs.scored_at` for ordering.
* `data/outputs/score_all_run.log` retains the raw log.
