import json
import socket
import urllib.request

companies = json.load(open("data/companies.json"))

for host in ("boards-api.eu.greenhouse.io", "boards-api.greenhouse.io", "api.eu.lever.co", "api.lever.co"):
    try:
        addrs = socket.gethostbyname_ex(host)
        print(f"DNS OK  {host} -> {addrs[2]}")
    except socket.gaierror as e:
        print(f"DNS FAIL {host} -> {e}")
print()

gh_slugs = companies.get("greenhouse_eu", [])
print(f"--- greenhouse_eu ({len(gh_slugs)} slugs) ---")
for slug in gh_slugs:
    for host in ("boards-api.eu.greenhouse.io", "boards-api.greenhouse.io"):
        url = f"https://{host}/v1/boards/{slug}/jobs"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-job-scan-debug"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                print(f"{slug:30s} {host:30s} HTTP {resp.status}  jobs={len(data.get('jobs', []))}")
        except urllib.error.HTTPError as e:
            print(f"{slug:30s} {host:30s} HTTP {e.code}")
        except Exception as e:
            print(f"{slug:30s} {host:30s} ERROR {type(e).__name__}: {e}")

lv_slugs = companies.get("lever_eu", [])
print(f"\n--- lever_eu ({len(lv_slugs)} slugs) ---")
for slug in lv_slugs:
    for host in ("api.eu.lever.co", "api.lever.co"):
        url = f"https://{host}/v0/postings/{slug}?mode=json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-job-scan-debug"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                n = len(data) if isinstance(data, list) else "?"
                print(f"{slug:30s} {host:30s} HTTP {resp.status}  jobs={n}")
        except urllib.error.HTTPError as e:
            print(f"{slug:30s} {host:30s} HTTP {e.code}")
        except Exception as e:
            print(f"{slug:30s} {host:30s} ERROR {type(e).__name__}: {e}")
