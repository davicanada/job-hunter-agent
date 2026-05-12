-- 2026-05-11: Brazil added to the target region (Canada/USA/Europe → Canada/USA/Europe/Brazil).
-- Davi is a Brazilian citizen, so LATAM / Latin-America / South-America postings are also
-- in scope (Brazil ⊂ LATAM). Reopen historical jobs that were stored with
-- allows_target_region = false purely because their location/description matched the
-- old "latam only" / "brazil only" / "must reside in latin america" blocks. Clearing
-- the flag to NULL lets the prefilter pass them through to the LLM scorer, which now
-- treats Brazil/LATAM postings as auth_status "ok_work_permit".
update jobs
set allows_target_region = null
where allows_target_region = false
  and lower(coalesce(location, '') || ' ' || coalesce(description, '')) ~
      '(brazil|brasil|latam|latin america|south america|são paulo|sao paulo|rio de janeiro)';
