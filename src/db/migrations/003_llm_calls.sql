-- Block 3.5: per-call LLM telemetry. Every provider call (success or error)
-- inserts one row so we can see which provider + model served which stage of
-- a run, how hot token usage is running, and which providers go down first.
-- Run this once in the Supabase SQL editor before the first Block 3.5 cycle.

create table if not exists llm_calls (
    id uuid primary key default gen_random_uuid(),
    run_id uuid references runs(id) on delete set null,
    stage text not null,
    provider text not null,
    model text not null,
    input_tokens integer not null default 0,
    output_tokens integer not null default 0,
    latency_ms integer not null default 0,
    success boolean not null default true,
    error text,
    created_at timestamptz not null default now()
);

create index if not exists idx_llm_calls_run on llm_calls(run_id);
create index if not exists idx_llm_calls_provider on llm_calls(provider);
create index if not exists idx_llm_calls_stage on llm_calls(stage);
create index if not exists idx_llm_calls_created_at on llm_calls(created_at desc);
