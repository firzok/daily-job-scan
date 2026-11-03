#!/usr/bin/env python3
"""Render a lean read-only display page from data/job-db.json.

Shows totals plus a table of entries first seen in the last N days (default 30).
The JSON file is the source of truth; this page is regenerated every run and
republished to the same artifact URL.

    python3 -I scripts/render_page.py [--days 30] [--out out/job-log.html]
"""
import argparse
import collections
import datetime as dt
import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSS = """
/* Layout: one centered column, summary tiles, then a single scrollable table. */
:root {
  --bg: #FAFAF7; --surface: #FFFFFF; --sunken: #F1F0EA;
  --text: #1C1E1C; --muted: #63665F; --faint: #8A8D85;
  --border: #E2E0D6; --border-strong: #C9C6B8;
  --accent: #2E6E5E; --accent-strong: #1F4E42; --accent-soft: #E1EDE7;
  --warn: #A6650F; --bad: #A23B2E; --info: #3D5A99;
  --f-display: "Zilla Slab", Georgia, serif;
  --f-body: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #14161A; --surface: #1B1E22; --sunken: #22252A; --text: #ECEBE5; --muted: #A4A79D; --faint: #767970;
  --border: #2C2F33; --border-strong: #3A3E42; --accent: #5FA491; --accent-strong: #7FBBA9; --accent-soft: #21322C;
  --warn: #D6A24E; --bad: #D97A6A; --info: #8FA8E0; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #14161A; --surface: #1B1E22; --sunken: #22252A; --text: #ECEBE5; --muted: #A4A79D; --faint: #767970;
  --border: #2C2F33; --border-strong: #3A3E42; --accent: #5FA491; --accent-strong: #7FBBA9; --accent-soft: #21322C;
  --warn: #D6A24E; --bad: #D97A6A; --info: #8FA8E0; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--f-body); line-height: 1.5; }
.wrap { max-width: 1100px; margin: 0 auto; padding-block: 40px 80px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
header { display: flex; flex-direction: column; gap: 8px; padding-bottom: 22px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
.eyebrow { font-family: var(--f-mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
h1 { font-family: var(--f-display); font-weight: 700; font-size: clamp(26px, 5vw, 34px); line-height: 1.1; margin: 0; text-wrap: balance; }
.dek { color: var(--muted); font-size: 15px; max-width: 68ch; margin: 0; }
.stats { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 9px 14px; min-width: 100px; }
.stat .n { font-family: var(--f-mono); font-size: 20px; font-variant-numeric: tabular-nums; }
.stat .l { font-size: 11px; color: var(--faint); text-transform: uppercase; letter-spacing: .05em; }
h2 { font-family: var(--f-display); font-size: 20px; margin: 0 0 12px; }
.scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; min-width: 880px; font-size: 13px; }
th { text-align: left; font-family: var(--f-mono); font-weight: 500; font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  color: var(--faint); background: var(--sunken); padding: 9px 12px; border-bottom: 1px solid var(--border-strong); white-space: nowrap; }
td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:last-child td { border-bottom: none; }
.co { font-weight: 600; }
.num { font-family: var(--f-mono); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.notes { color: var(--muted); font-size: 12px; max-width: 32ch; }
.st { font-family: var(--f-mono); font-size: 11px; white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; }
.st::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.st-reported { color: var(--accent-strong); } .st-excluded { color: var(--bad); }
.st-shortlisted { color: var(--warn); } .st-applied { color: var(--info); }
a { color: var(--accent); text-decoration: none; border-bottom: 1px solid color-mix(in srgb, var(--accent) 40%, transparent); }
a:hover { border-bottom-color: var(--accent); }
a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
footer { margin-top: 32px; color: var(--faint); font-size: 12px; font-family: var(--f-mono); }
"""


def esc(s):
    return html.escape(str(s or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=os.path.join(ROOT, "out/job-log.html"))
    a = ap.parse_args()
    with open(os.path.join(ROOT, "data/job-db.json"), encoding="utf-8") as f:
        db = json.load(f)
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=a.days)).isoformat()
    counts = collections.Counter(e.get("status") for e in db)
    recent = sorted((e for e in db if e.get("first_seen", "") >= cutoff),
                    key=lambda e: (e.get("first_seen", ""), e.get("status") != "excluded"), reverse=True)
    last = max((e.get("first_seen", "") for e in db), default="")
    rows = []
    for e in recent:
        posted = (e.get("posted_at") or "")[:10] or "unknown"
        link = f'<a href="{esc(e.get("link"))}" target="_blank" rel="noopener">Open</a>' if e.get("link") else ""
        rows.append(
            f'<tr><td class="co">{esc(e.get("company"))}</td><td>{esc(e.get("role"))}</td>'
            f'<td>{esc(e.get("location"))}</td><td class="num">{esc(posted)}</td>'
            f'<td class="num">{esc(e.get("first_seen"))}</td>'
            f'<td><span class="st st-{esc(e.get("status"))}">{esc(e.get("status"))}</span></td>'
            f'<td class="notes">{esc(e.get("notes"))}</td><td>{link}</td></tr>')
    tiles = [("Total logged", len(db)), ("Reported", counts["reported"]), ("Excluded", counts["excluded"]),
             ("Shortlisted", counts["shortlisted"]), ("Applied", counts["applied"]), (f"Last {a.days} days", len(recent))]
    page = f"""<title>Job Search Log</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Zilla+Slab:wght@600;700&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header>
    <div class="eyebrow">Daily fresh-jobs scan &middot; updated {esc(last)}</div>
    <h1>Job Search Log</h1>
    <p class="dek">Frontend and fullstack roles found in job boards' own feeds. This page is regenerated from <code>data/job-db.json</code> after every run and shows the last {a.days} days.</p>
    <div class="stats">{"".join(f'<div class="stat"><div class="n">{n}</div><div class="l">{esc(l)}</div></div>' for l, n in tiles)}</div>
  </header>
  <h2>Last {a.days} days</h2>
  <div class="scroll"><table>
    <thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Posted</th><th>First seen</th><th>Status</th><th>Notes</th><th>Apply</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
  <footer>Read-only view. The full log of {len(db)} entries lives in data/job-db.json.</footer>
</div>
"""
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {a.out}: {len(recent)} rows, {len(page)//1024}KB")


if __name__ == "__main__":
    main()
