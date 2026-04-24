alter table scored_jobs
  add column if not exists recency_bonus int default 0,
  add column if not exists age_days int;
