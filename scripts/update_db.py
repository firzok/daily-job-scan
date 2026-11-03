#!/usr/bin/env python3
"""Append reviewed results to data/job-db.json.

Input: a decisions JSON (written by Claude after reviewing out/candidates.json):
[
  {"id": "<candidate id>", "status": "reported" | "excluded", "notes": "...",
   # optional overrides of what the API gave:
   "company": "...", "role": "...", "location": "...", "experience": "...",
   "stack": "...", "comp": "..."}
]
Fields not overridden are filled from the candidate. Entries already in the db
(by id or link) are skipped. Status can later move reported -> shortlisted -> applied.

    python3 -I scripts/update_db.py out/decisions.json
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_jobs import ROOT, make_id, norm_url  # noqa: E402

FIELDS = ["id", "company", "role", "location", "experience", "stack", "comp", "source",
          "link", "posted_at", "first_seen", "status", "notes"]


def main():
    dec_path = sys.argv[1]
    cand_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "out/candidates.json")
    db_path = os.path.join(ROOT, "data/job-db.json")
    with open(dec_path, encoding="utf-8") as f:
        decisions = json.load(f)
    with open(cand_path, encoding="utf-8") as f:
        c = json.load(f)
    pool = {p["id"]: p for k in ("candidates", "needs_review", "excluded") for p in c.get(k, [])}
    with open(db_path, encoding="utf-8") as f:
        db = json.load(f)
    ids = {e["id"] for e in db}
    urls = {norm_url(e.get("link", "")) for e in db}
    today = dt.date.today().isoformat()
    added = 0
    for d in decisions:
        p = pool.get(d["id"], {})
        if d.get("status") not in ("reported", "excluded"):
            sys.exit(f"bad status for {d['id']}")
        e = {
            "company": d.get("company") or p.get("company", ""),
            "role": d.get("role") or p.get("title", ""),
            "location": d.get("location") or p.get("location", ""),
            "experience": d.get("experience") or (f"{p['years']}+ yrs" if p.get("years") else "Not stated"),
            "stack": d.get("stack", ""),
            "comp": d.get("comp") or p.get("comp", ""),
            "source": d.get("source") or f"{p.get('source', '')} API",
            "link": d.get("link") or p.get("url", ""),
            "posted_at": p.get("posted_at") or d.get("posted_at", ""),
            "first_seen": today,
            "status": d["status"],
            "notes": d.get("notes", ""),
        }
        e["id"] = make_id(e["company"], e["role"]) if d.get("company") or d.get("role") else d["id"]
        if e["id"] in ids or (e["link"] and norm_url(e["link"]) in urls):
            continue
        db.append({k: e[k] for k in FIELDS})
        ids.add(e["id"])
        urls.add(norm_url(e["link"]))
        added += 1
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"added {added}; db now {len(db)} entries")


if __name__ == "__main__":
    main()
