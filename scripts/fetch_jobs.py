#!/usr/bin/env python3
"""Step 1: fetch every posting we can get from all sources, newest first. No filtering.

    python3 -I scripts/fetch_jobs.py [--out out/postings.json] [--sources ashby,lever] [--max-slugs 5]

Writes {"summary": {...}, "postings": [...]} where postings are normalized (see
jobscan/sources.py) and sorted by age_hours ascending (undated last). Also appends any
new ATS slugs found in aggregator postings to data/companies.json (--no-discover to skip).
Exit 2 if zero sources were reachable.
"""
import argparse, datetime as dt, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from jobscan.sources import fetch_all, finalize, discover_slugs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", default=os.path.join(ROOT, "data/companies.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out/postings.json"))
    ap.add_argument("--sources", default="all")
    ap.add_argument("--max-slugs", type=int, default=0)
    ap.add_argument("--fixtures")
    ap.add_argument("--no-discover", action="store_true")
    a = ap.parse_args()
    with open(a.companies, encoding="utf-8") as f:
        companies = json.load(f)
    sources = None if a.sources == "all" else set(a.sources.split(","))
    raw, status = fetch_all(companies, sources, a.max_slugs, a.fixtures)
    postings = finalize(raw)
    added = [] if a.no_discover else discover_slugs(raw, companies)
    if added and not a.fixtures:
        with open(a.companies, "w", encoding="utf-8") as f:
            json.dump(companies, f, ensure_ascii=False, indent=2); f.write("\n")
    ok = [k for k, v in status.items() if v["ok"]]
    per_source = {}
    for p in postings:
        per_source[p["source"]] = per_source.get(p["source"], 0) + 1
    summary = {
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources_ok": len(ok), "sources_failed": len(status) - len(ok),
        "postings": len(postings), "undated": sum(p["age_hours"] is None for p in postings),
        "per_source": per_source, "new_slugs": added,
        "dead_slugs": sorted(k for k, v in status.items() if v.get("dead_slug")),
        "failed_sources": {k: v["error"] for k, v in sorted(status.items()) if not v["ok"] and not v.get("dead_slug")},
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "postings": postings}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not ok:
        print("ERROR: zero sources reachable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
