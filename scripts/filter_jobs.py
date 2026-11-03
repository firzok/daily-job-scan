#!/usr/bin/env python3
"""Step 2: filter fetched postings with a profile.

    python3 -I scripts/filter_jobs.py --profile profiles/firzok.json \
        [--in out/postings.json] [--out out/candidates.json] [--max-age-hours 360] [--no-dedupe]

Writes {"summary", "match", "review", "excluded"}. By default skips anything already in
data/job-db.json (by id or URL); --no-dedupe lists everything that fits.
"""
import argparse, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from jobscan.filters import Profile, apply  # noqa: E402
from jobscan.sources import norm_url  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--in", dest="inp", default=os.path.join(ROOT, "out/postings.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out/candidates.json"))
    ap.add_argument("--db", default=os.path.join(ROOT, "data/job-db.json"))
    ap.add_argument("--max-age-hours", type=float, help="override the profile's max_age_hours")
    ap.add_argument("--no-dedupe", action="store_true")
    a = ap.parse_args()
    pr = Profile.load(a.profile)
    if a.max_age_hours is not None:
        pr.max_age = a.max_age_hours
    with open(a.inp, encoding="utf-8") as f:
        postings = json.load(f)["postings"]
    ids, urls = set(), set()
    if not a.no_dedupe and os.path.exists(a.db):
        with open(a.db, encoding="utf-8") as f:
            db = json.load(f)
        ids = {e["id"] for e in db}
        urls = {norm_url(e["link"]) for e in db if e.get("link")}
    res = apply(postings, pr, ids, urls)
    summary = {"profile": pr.name, "max_age_hours": pr.max_age, "input_postings": len(postings),
               "deduped_against_log": not a.no_dedupe, **{k: len(v) for k, v in res.items()}}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, **res}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
