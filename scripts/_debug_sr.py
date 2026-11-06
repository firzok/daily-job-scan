import json
import urllib.request

for slug in ("bertelsmann-jobs", "avaloq1", "ScalableGmbH"):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "daily-job-scan-debug"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    print(f"=== {slug} ===")
    content = data.get("content", [])
    if content:
        print(json.dumps(content[0], indent=2)[:3000])
    else:
        print("no postings")
    print()
