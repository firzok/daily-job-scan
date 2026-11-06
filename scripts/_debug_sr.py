import json
import sys

sys.path.insert(0, ".")
from jobscan.sources import fetch_all
from jobscan.enrich import employment_type

companies = json.load(open("data/companies.json"))
postings, status = fetch_all(companies)
print(f"fetched {len(postings)} postings\n")

bad = 0
for p in postings:
    emp = p.get("employment")
    if not isinstance(emp, (str, type(None))):
        bad += 1
        print(f"BAD employment field: source={p.get('source')} slug={p.get('slug')} "
              f"title={p.get('title')!r} employment={emp!r} (type {type(emp).__name__})")
        if bad >= 20:
            break

if not bad:
    print("no bad employment fields found among fetched postings")
    print("re-checking with employment_type() directly to see if it still throws anywhere...")
    for p in postings:
        try:
            employment_type(p.get("employment"), p.get("title") or "")
        except Exception as e:
            print(f"CRASH: source={p.get('source')} slug={p.get('slug')} title={p.get('title')!r} "
                  f"employment={p.get('employment')!r} -> {type(e).__name__}: {e}")
