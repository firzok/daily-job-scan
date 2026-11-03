#!/usr/bin/env python3
"""Step 3 (ApplyX feed): push fetched postings into ApplyX's shared `job_postings` table.

    SUPABASE_URL=... SUPABASE_SECRET_KEY=... python3 -I scripts/push_supabase.py [--in out/postings.json]
    python3 -I scripts/push_supabase.py --dry-run     # build rows, print stats, send nothing

Only compact rows are sent (tags + a 300-char snippet, no full descriptions; see
jobscan/enrich.py). Postings older than the window are skipped, and the window shrinks
from 30 to 21 to 14 days when the database grows past the size guard.

RPCs used (all service-role only, defined in ApplyX's migration):
  job_feed_status()                       -> {db_bytes, open_postings}
  upsert_postings(rows jsonb)             -> {inserted, updated, unchanged}
  prune_postings(window_days, stale_hours) -> {closed, deleted}
  log_fetch_run(run jsonb)
Exit 1 on any failed RPC, so the workflow run shows red.
"""
import argparse, datetime as dt, json, os, sys, urllib.error, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from jobscan.enrich import to_row  # noqa: E402
from jobscan.sources import parse_date  # noqa: E402

# Remotive's terms forbid showing its jobs behind a signup; Workable rate-limits us.
EXCLUDED_SOURCES = {"remotive", "workable"}
WINDOWS = [(350 * 2**20, 14), (250 * 2**20, 21), (0, 30)]  # (db bytes above, window days)
STALE_HOURS = 60   # not seen for ~2.5 daily runs -> closed (one failed run is not enough)
BATCH = 500


class Supabase:
    def __init__(self, url, key, timeout=120):
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.headers = {"apikey": key, "Content-Type": "application/json", "Accept": "application/json"}
        if key.startswith("eyJ"):  # legacy service_role JWT
            self.headers["Authorization"] = f"Bearer {key}"
        self.timeout = timeout

    def rpc(self, fn, args):
        req = urllib.request.Request(self.base + fn, data=json.dumps(args, default=str).encode(),
                                     headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode() or "null"
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{fn}: HTTP {e.code} {e.read().decode()[:500]}") from None
        return json.loads(body)


def pick_window(db_bytes):
    return next(days for limit, days in WINDOWS if db_bytes >= limit)


def build_rows(postings, window_days, now):
    cutoff = now - dt.timedelta(days=window_days)
    rows, skipped = {}, {"excluded_source": 0, "too_old": 0, "no_url": 0}
    for p in postings:
        if p.get("source") in EXCLUDED_SOURCES:
            skipped["excluded_source"] += 1
            continue
        if not p.get("url"):
            skipped["no_url"] += 1
            continue
        d = parse_date(p.get("posted_at"))
        if d and d < cutoff:
            skipped["too_old"] += 1
            continue
        r = to_row(p)
        rows[(r["source"], r["external_id"])] = r  # last one wins on a repeated key
    return list(rows.values()), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=os.path.join(ROOT, "out/postings.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--window-days", type=int, help="override the size-guarded window")
    a = ap.parse_args()
    with open(a.inp, encoding="utf-8") as f:
        data = json.load(f)
    now = dt.datetime.now(dt.timezone.utc)
    summary = data.get("summary", {})

    sb = None
    if not a.dry_run:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
        if not url or not key:
            print("ERROR: SUPABASE_URL and SUPABASE_SECRET_KEY must be set", file=sys.stderr)
            return 1
        sb = Supabase(url, key)
    status = sb.rpc("job_feed_status", {}) if sb else {"db_bytes": 0, "open_postings": 0}
    window = a.window_days or pick_window(status.get("db_bytes") or 0)
    rows, skipped = build_rows(data["postings"], window, now)
    approx_kb = len(json.dumps(rows, ensure_ascii=False).encode()) // 1024
    print(f"window {window}d, {len(rows)} rows (~{approx_kb} KB), skipped {skipped}, db {status}")
    if a.dry_run:
        return 0

    totals = {"inserted": 0, "updated": 0, "unchanged": 0}
    for i in range(0, len(rows), BATCH):
        res = sb.rpc("upsert_postings", {"rows": rows[i:i + BATCH]}) or {}
        for k in totals:
            totals[k] += res.get(k, 0)
    # A job is closed only after it has been missing for STALE_HOURS, so one failed run
    # at a board never removes its jobs.
    pruned = sb.rpc("prune_postings", {"window_days": window, "stale_hours": STALE_HOURS}) or {}
    after = sb.rpc("job_feed_status", {})
    run = {
        "run_at": summary.get("run_at") or now.isoformat(), "window_days": window,
        "sources_ok": summary.get("sources_ok"), "sources_failed": summary.get("sources_failed"),
        "failed_sources": summary.get("failed_sources", {}), "dead_slugs": summary.get("dead_slugs", []),
        "rows_sent": len(rows), **totals, "closed": pruned.get("closed", 0), "deleted": pruned.get("deleted", 0),
        "db_bytes": after.get("db_bytes"), "open_postings": after.get("open_postings"),
    }
    sb.rpc("log_fetch_run", {"run": run})
    print(json.dumps(run, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
