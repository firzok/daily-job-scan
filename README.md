# daily-job-scan

Daily scanner for new frontend/fullstack engineering roles, read straight from job boards'
own public feeds (Greenhouse, Ashby, Lever, Workable, Recruitee, Teamtailor, Remotive,
RemoteOK, We Work Remotely, Arbeitnow, Himalayas, HN "Who is hiring").

- `data/job-db.json`: the persistent log and dedupe source of truth. Entry fields: id,
  company, role, location, experience, stack, comp, source, link, posted_at, first_seen,
  status (`reported` | `excluded`, later `shortlisted` | `applied`), notes.
  id = lowercase slug of `company-role`.
- `data/companies.json`: ATS slugs to poll, grouped by ATS. Grows automatically when the
  scan finds ATS links in aggregator postings.
Two independent steps, so the filter can change (or come from a user profile in ApplyX)
without touching the fetch code:

1. `scripts/fetch_jobs.py` (`jobscan/sources.py`): fetch every posting from every source,
   normalize, sort newest first, write `out/postings.json`. No filtering.
2. `scripts/filter_jobs.py --profile profiles/firzok.json` (`jobscan/filters.py`): apply a
   JSON profile (age limit, title, seniority, employment type, tech, years, location rules
   with priorities), dedupe against the log, write `out/candidates.json` with match /
   review / excluded. `--max-age-hours` overrides the profile's age limit.
3. `scripts/push_supabase.py` (`jobscan/enrich.py`): turn each posting into a compact row
   (tags for filtering + a 300-char snippet, no full description), keep the last 30 days, and
   upsert into ApplyX's shared `job_postings` table. Needs `SUPABASE_URL` and
   `SUPABASE_SECRET_KEY`; `--dry-run` sends nothing. Remotive and Workable are left out
   (Remotive's terms forbid showing its jobs behind a signup; Workable rate-limits us).
   `.github/workflows/daily-fetch.yml` runs steps 1 and 3 every day at 05:00 UTC.
- `scripts/report.py`: print candidates as a Markdown table.
- `scripts/update_db.py`: append reviewed decisions to the log.
- `scripts/render_page.py`: build the read-only display page (last 30 days + totals).
- `ROUTINE.md`: the daily routine prompt.

## Criteria
Include: frontend or fullstack, mid/senior, at most ~8 years required. Worldwide remote, broad
region remote (Europe, Asia, EMEA), remote in Germany, or Berlin onsite/hybrid.
Exclude: .NET or PHP primary; remote limited to one country, a country list, or the Americas;
Staff/Principal/Lead/Head or >8 years; contract/freelance/part-time; backend-only; non-engineering.

## Date fields
Greenhouse uses `first_published` when present, otherwise `updated_at` (flagged: edits move it).
Ashby `publishedAt`, Lever `createdAt`, Remotive `publication_date` (feed is delayed ~24h),
RemoteOK `epoch`, WWR/Teamtailor `pubDate`, Arbeitnow `created_at`, Himalayas `pubDate`.

## Phase 2 (not built)
Applying. Every reported entry keeps its direct apply link and a status that can move
reported -> shortlisted -> applied. Each submission needs explicit approval.
