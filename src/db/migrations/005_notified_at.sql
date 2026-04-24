alter table applications
  add column if not exists notified_at timestamptz;

create index if not exists applications_notified_at_idx
  on applications (notified_at);
