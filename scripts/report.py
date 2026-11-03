#!/usr/bin/env python3
"""Print candidates as a Markdown table (match first, then review).

    python3 -I scripts/report.py [out/candidates.json] [--include-review] > report.md
"""
import json, sys

def cell(s, n=None):
    s = str(s or "").replace("|", "/").replace("\n", " ").strip()
    return (s[:n] + "…") if n and len(s) > n else s

def main():
    path = next((x for x in sys.argv[1:] if not x.startswith("--")), "out/candidates.json")
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    groups = [("Matches", c["match"])] + ([("Needs review", c["review"])] if "--include-review" in sys.argv else [])
    for title, rows in groups:
        print(f"\n## {title} ({len(rows)})\n")
        print("| Company | Role | Location | Experience | Description | Comp | Posted (API field) | Apply |")
        print("|---|---|---|---|---|---|---|---|")
        for p in rows:
            yrs = f"{p['years']}+ yrs" if p.get("years") else "not stated"
            posted = f"{(p.get('posted_at') or '')[:10]} ({p.get('date_field')})"
            print(f"| {cell(p['company'])} | {cell(p['title'], 70)} | {cell(p['location'], 50)} ({cell(p.get('location_class'))}) | {yrs} "
                  f"| {cell(p.get('description'), 120)} | {cell(p.get('comp'))} | {posted} | [link]({p['url']}) |")

main()
