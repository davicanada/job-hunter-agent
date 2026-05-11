-- Broaden the geographic filter from Canada-only to Canada/USA/Europe by
-- renaming the boolean column. Existing values stay valid: a True from the
-- old Canada-only check remains True under the broader scope, since
-- Canada-friendly postings are a subset of target-region-friendly postings.
alter table jobs rename column allows_canada to allows_target_region;
