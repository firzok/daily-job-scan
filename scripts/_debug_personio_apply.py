import re
import urllib.request

URL = "https://gokarla-gmbh.jobs.personio.de/job/2515695?display=en&language=en"

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (debug)"})
with urllib.request.urlopen(req, timeout=20) as resp:
    html = resp.read().decode("utf-8", "replace")

print(f"status ok, {len(html)} bytes\n")

# Look for anything mentioning "apply" in hrefs, data attrs, or button text context
for m in re.finditer(r'.{80}apply.{80}', html, re.IGNORECASE):
    print(m.group(0).replace("\n", " "))
    print("---")
