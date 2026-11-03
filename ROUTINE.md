# Daily routine prompt

Run the daily fresh-jobs scan in this repo (firzok/daily-job-scan). Nobody is watching this run.

1. `python3 -I scripts/fetch_jobs.py` from the repo root. It writes `out/candidates.json`
   (summary, candidates, needs_review, excluded). If it exits non-zero or `sources_ok` is 0,
   send a PushNotification saying the run failed and why (include `failed_sources`), then stop.
2. Review every entry in `candidates` and `needs_review` against the criteria in README.md.
   Read the description (open the posting URL only if the description is missing). Drop
   false positives. For `needs_review`, decide reported or excluded; leave nothing undecided.
3. Write `out/decisions.json` (format in `scripts/update_db.py`): every confirmed match as
   `reported`, and notable rejections as `excluded` with a short reason in `notes`. Fill
   `company` (real name, not the slug), `experience`, `stack` and `comp` where known.
4. `python3 -I scripts/update_db.py out/decisions.json`, then commit
   `data/job-db.json` and `data/companies.json` and push to `main`.
5. `python3 -I scripts/render_page.py`, then republish `out/job-log.html` with the Artifact
   tool to `url` https://claude.ai/code/artifact/dfc78b94-11f8-411d-9eac-c798d2bdded4.
6. If there are new reported matches, send one PushNotification wrapped in
   `<routine_summary>`: the first sentence is the headline ("3 new remote frontend roles posted
   in the last 48h"), then one line per job: company / role / location / link, worldwide
   remote first, then Europe/Asia/Germany remote, then Berlin onsite/hybrid.
   If there are zero new matches, do not notify. Many failed sources (but not all) go in a
   one-line note at the end of the notification only if one is being sent anyway.

Final report table (in the session): company, role, location, required experience, short
description, compensation, posted date (from the API, with the date field used), apply link.
