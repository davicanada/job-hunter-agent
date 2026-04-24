-- Block 3: track jobs that never became applications and why.
-- Run this once in the Supabase SQL editor before the first Block 3 cycle.

create table if not exists skipped_jobs (
    id uuid primary key default gen_random_uuid(),
    job_id uuid references jobs(id) on delete cascade unique,
    skip_reason text not null,
    skip_stage text not null,
    skipped_at timestamptz default now()
);

create index if not exists idx_skipped_jobs_stage on skipped_jobs(skip_stage);
