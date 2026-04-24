-- job-hunter-agent database schema
-- Run this in the Supabase SQL editor after creating a new project.
-- Safe to re-run: all objects use IF NOT EXISTS / CREATE OR REPLACE.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================
-- jobs: every unique job seen across runs
-- ============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id     TEXT NOT NULL,
    source          TEXT NOT NULL,
    source_job_id   TEXT,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    is_remote       BOOLEAN NOT NULL DEFAULT TRUE,
    allows_canada   BOOLEAN,
    salary_min      INT,
    salary_max      INT,
    salary_currency TEXT,
    employment_type TEXT,
    description     TEXT NOT NULL,
    url             TEXT NOT NULL,
    posted_at       TIMESTAMPTZ,
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_data        JSONB,
    CONSTRAINT jobs_source_external_id_key UNIQUE (source, external_id),
    CONSTRAINT jobs_source_check CHECK (source IN (
        'remoteok', 'wwr', 'remotive', 'working_nomads', 'jobicy', 'himalayas'
    )),
    CONSTRAINT jobs_employment_type_check CHECK (
        employment_type IS NULL OR employment_type IN (
            'full-time', 'part-time', 'contract', 'temporary', 'internship'
        )
    )
);

CREATE INDEX IF NOT EXISTS jobs_discovered_at_idx ON jobs (discovered_at DESC);
CREATE INDEX IF NOT EXISTS jobs_company_idx        ON jobs (company);
CREATE INDEX IF NOT EXISTS jobs_source_idx         ON jobs (source);

-- ============================================================================
-- scored_jobs: LLM scoring output
-- ============================================================================
CREATE TABLE IF NOT EXISTS scored_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    score       INT  NOT NULL CHECK (score BETWEEN 0 AND 100),
    verdict     TEXT NOT NULL CHECK (verdict IN ('strong_match', 'stretch', 'skip')),
    track       TEXT CHECK (track IN (
        'analytics_engineer', 'ops_data_analyst', 'automation_dev', 'data_engineer', 'other'
    )),
    why_match   TEXT,
    watch_out   TEXT,
    auth_status TEXT CHECK (auth_status IN (
        'ok_work_permit', 'future_pr', 'blocked_citizen_only', 'unclear'
    )),
    scored_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model       TEXT
);

CREATE INDEX IF NOT EXISTS scored_jobs_job_id_idx ON scored_jobs (job_id);
CREATE INDEX IF NOT EXISTS scored_jobs_score_idx  ON scored_jobs (score DESC);

-- ============================================================================
-- applications: what the user did with each scored job
-- ============================================================================
CREATE TABLE IF NOT EXISTS applications (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scored_job_id     UUID NOT NULL UNIQUE REFERENCES scored_jobs(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN (
        'suggested', 'applied', 'skipped', 'regenerate_requested',
        'interview', 'rejected', 'ghosted'
    )),
    resume_path       TEXT,
    cover_letter_path TEXT,
    notes             TEXT,
    applied_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS applications_status_idx     ON applications (status);
CREATE INDEX IF NOT EXISTS applications_updated_at_idx ON applications (updated_at DESC);

-- Auto-update applications.updated_at on UPDATE
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS applications_touch_updated_at ON applications;
CREATE TRIGGER applications_touch_updated_at
    BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ============================================================================
-- runs: one row per cron invocation
-- ============================================================================
CREATE TABLE IF NOT EXISTS runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    jobs_fetched   INT NOT NULL DEFAULT 0,
    jobs_new       INT NOT NULL DEFAULT 0,
    jobs_scored    INT NOT NULL DEFAULT 0,
    jobs_notified  INT NOT NULL DEFAULT 0,
    errors         JSONB,
    status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN (
        'running', 'success', 'partial', 'failed'
    ))
);

CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs (started_at DESC);
