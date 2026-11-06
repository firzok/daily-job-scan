# daily-job-scan

Daily scanner for new frontend/fullstack engineering roles, read straight from job boards'
own public feeds (Greenhouse, Ashby, Lever, Workable, Recruitee, Teamtailor, SmartRecruiters,
BambooHR, Personio, Breezy HR, Remotive, RemoteOK, We Work Remotely, Arbeitnow, Himalayas,
Jobicy, HN "Who is hiring").

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
   `.github/workflows/daily-fetch.yml` runs steps 1 and 3 every 12h, at 05:00 and 17:00 UTC.
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
RemoteOK `epoch`, WWR/Teamtailor `pubDate`, Arbeitnow `created_at`, Himalayas `pubDate`,
SmartRecruiters `releasedDate`, Personio `createdAt`, Breezy `published_date`, Jobicy `pubDate`.
BambooHR's public listing has no reliable post-date field at all (undated, sorts last).

## New-source caveat (SmartRecruiters, BambooHR, Personio, Breezy, Jobicy)
These five were added from each platform's documented public response shape, but this repo's
sandboxed dev environment could not reach any of the five hosts to verify live (egress was
blocked), so none of them were confirmed against a real response before merging -- only against
hand-built fixtures in `tests/test_sources.py` that match the documented shape. A bad field
name fails safe (that one source is marked `ok: false` in the run's `failed_sources` for that
day; nothing else breaks, nothing bad gets pushed), but double-check the first real
`daily-fetch` run's summary output (or run `python3 -I scripts/fetch_jobs.py --sources
smartrecruiters,bamboohr,personio,breezy,jobicy` locally) before trusting them long-term.
SmartRecruiters' and BambooHR's list endpoints don't return a full description (no per-job
snippet/tech-tag enrichment from those two); BambooHR also has no date field. No company
slugs were seeded for these four ATS kinds (unlike the others) since guessing real companies'
ATS slugs without a way to verify them felt worse than leaving it to `discover_slugs`, which
now also recognizes smartrecruiters.com / bamboohr.com / personio.de / breezy.hr links and
will grow the list organically the same way it already does for the rest.
Also worth checking before relying on Jobicy day to day: Remotive is excluded from the ApplyX
push specifically over its redistribution terms (see `scripts/push_supabase.py`) despite also
being a public API, so "publicly documented" alone isn't a guarantee Jobicy's terms allow this
use -- that wasn't independently confirmed here.

## Phase 2 (not built)
Applying. Every reported entry keeps its direct apply link and a status that can move
reported -> shortlisted -> applied. Each submission needs explicit approval.
