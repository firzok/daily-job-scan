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
- `scripts/fetch_jobs.py`: fetch, filter (48h window, title, seniority, tech, location),
  dedupe by id and URL, write `out/candidates.json`.
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
