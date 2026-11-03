"""Source adapters: fetch every posting from job boards' own public feeds and
normalize it to one shape. No filtering happens here beyond the time window.

Normalized posting fields:
  id, source, slug, company, title, location, remote (bool|None), url,
  posted_at (ISO UTC), date_field, date_note, description, comp, employment, age_hours
"""
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "fresh-jobs-scanner/1.0 (personal job search; stdlib urllib)"

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def slugify(s):
    s = s.lower()
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE)
    return re.sub(r"-+", "-", s).strip("-")


def make_id(company, role):
    return slugify(f"{company}-{role}")


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-encodes
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_date(v):
    """Return aware UTC datetime from ISO string, RFC822 string, or epoch (s/ms)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc)
    s = str(v).strip()
    if s.isdigit():
        return parse_date(int(s))
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def norm_url(u):
    if not u:
        return ""
    p = urllib.parse.urlsplit(u.strip())
    path = p.path.rstrip("/")
    path = re.sub(r"/(apply|application)$", "", path)
    return f"{p.netloc.lower().removeprefix('www.')}{path}".lower()


class Fetcher:
    def __init__(self, fixtures=None, timeout=25):
        self.fixtures = fixtures
        self.timeout = timeout

    def get(self, url, kind="json"):
        if self.fixtures:
            fn = os.path.join(self.fixtures, slugify(url)[:150] + (".json" if kind == "json" else ".xml"))
            if not os.path.exists(fn):
                raise FileNotFoundError(f"no fixture for {url}")
            with open(fn, encoding="utf-8") as f:
                data = f.read()
        else:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, application/xml, */*"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read().decode("utf-8", "replace")
        return json.loads(data) if kind == "json" else data


# --------------------------------------------------------------------------
# source adapters -> normalized postings
# Each posting: source, company, slug, title, location, remote(bool|None),
# url, posted_at(iso), date_field, date_note, description, comp, employment
# --------------------------------------------------------------------------

def P(**kw):
    kw.setdefault("remote", None)
    kw.setdefault("comp", "")
    kw.setdefault("employment", "")
    kw.setdefault("date_note", "")
    kw.setdefault("description", "")
    d = kw.get("posted")
    kw["posted_at"] = d.isoformat() if d else None
    kw.pop("posted", None)
    return kw


def src_greenhouse(f, slug, eu=False):
    host = "boards-api.eu.greenhouse.io" if eu else "boards-api.greenhouse.io"
    data = f.get(f"https://{host}/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        fp = parse_date(j.get("first_published"))
        up = parse_date(j.get("updated_at"))
        posted, field, note = (fp, "first_published", "") if fp else (up, "updated_at", "updated_at changes on edits; real post date may be older")
        out.append(P(source="greenhouse", slug=slug, company=j.get("company_name") or slug,
                     title=j.get("title", ""), location=(j.get("location") or {}).get("name", ""),
                     url=j.get("absolute_url", ""), posted=posted, date_field=field, date_note=note,
                     description=strip_html(j.get("content", ""))))
    return out


def src_ashby(f, slug):
    data = f.get(f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
        out.append(P(source="ashby", slug=slug, company=slug, title=j.get("title", ""),
                     location=" | ".join(x for x in locs if x) or addr.get("addressCountry", ""),
                     remote=j.get("isRemote"), url=j.get("jobUrl", ""),
                     posted=parse_date(j.get("publishedAt")), date_field="publishedAt",
                     description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
                     comp=comp, employment=j.get("employmentType", "")))
    return out


def src_lever(f, slug, eu=False):
    host = "api.eu.lever.co" if eu else "api.lever.co"
    data = f.get(f"https://{host}/v0/postings/{slug}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        sr = j.get("salaryRange") or {}
        comp = f"{sr.get('currency','')} {sr.get('min','')}-{sr.get('max','')} {sr.get('interval','')}".strip() if sr else ""
        out.append(P(source="lever", slug=slug, company=slug, title=j.get("text", ""),
                     location=" | ".join(l for l in locs if l), remote=(j.get("workplaceType") == "remote") or None,
                     url=j.get("hostedUrl", ""), posted=parse_date(j.get("createdAt")), date_field="createdAt",
                     description=j.get("descriptionPlain", "") + " " + strip_html(" ".join(l.get("content", "") for l in j.get("lists", []))),
                     comp=comp, employment=cat.get("commitment", "")))
    return out


def src_workable(f, slug):
    data = f.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        out.append(P(source="workable", slug=slug, company=data.get("name") or slug, title=j.get("title", ""),
                     location=loc, remote=j.get("telecommuting"), url=j.get("url") or j.get("application_url", ""),
                     posted=parse_date(j.get("published_on") or j.get("created_at")), date_field="published_on",
                     description=strip_html(j.get("description", "")), employment=j.get("employment_type", "")))
    return out


def src_recruitee(f, slug):
    data = f.get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        out.append(P(source="recruitee", slug=slug, company=j.get("company_name") or slug, title=j.get("title", ""),
                     location=j.get("location", ""), remote=j.get("remote"), url=j.get("careers_url", ""),
                     posted=parse_date(j.get("published_at") or j.get("created_at")), date_field="published_at",
                     description=strip_html(j.get("description", "") + " " + j.get("requirements", "")),
                     employment=j.get("employment_type_code", "")))
    return out


def src_teamtailor(f, slug):
    xml = f.get(f"https://{slug}.teamtailor.com/jobs.rss", kind="xml")
    return _rss(xml, "teamtailor", slug, company=slug)


def _rss(xml, source, slug, company=None):
    out = []
    root = ET.fromstring(xml)
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()
        title = g("title")
        comp_name = company
        if source == "weworkremotely" and ":" in title:  # "Company: Role"
            comp_name, title = [x.strip() for x in title.split(":", 1)]
        out.append(P(source=source, slug=slug, company=comp_name or "", title=title,
                     location=g("region") or g("{http://www.w3.org/2005/Atom}location") or g("location"),
                     url=g("link"), posted=parse_date(g("pubDate")), date_field="pubDate",
                     description=strip_html(g("description")), employment=g("type")))
    return out


def src_wwr(f, _=None):
    out = []
    for cat in ("remote-front-end-programming-jobs", "remote-full-stack-programming-jobs"):
        out += _rss(f.get(f"https://weworkremotely.com/categories/{cat}.rss", kind="xml"), "weworkremotely", cat)
    return out


def src_remotive(f, _=None):
    data = f.get("https://remotive.com/api/remote-jobs?category=software-dev&limit=300")
    return [P(source="remotive", slug="remotive", company=j.get("company_name", ""), title=j.get("title", ""),
              location=j.get("candidate_required_location", ""), remote=True, url=j.get("url", ""),
              posted=parse_date(j.get("publication_date")), date_field="publication_date",
              description=strip_html(j.get("description", "")), comp=j.get("salary", ""),
              employment=j.get("job_type", "")) for j in data.get("jobs", [])]


def src_remoteok(f, _=None):
    data = f.get("https://remoteok.com/api")
    out = []
    for j in data[1:] if isinstance(data, list) else []:  # first item is legal notice
        comp = f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else ""
        out.append(P(source="remoteok", slug="remoteok", company=j.get("company", ""), title=j.get("position", ""),
                     location=j.get("location", ""), remote=True, url=j.get("apply_url") or j.get("url", ""),
                     posted=parse_date(j.get("epoch") or j.get("date")), date_field="epoch",
                     description=strip_html(j.get("description", "")) + " tags: " + ", ".join(j.get("tags") or []),
                     comp=comp))
    return out


def src_arbeitnow(f, _=None):
    out = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    for _page in range(3):
        data = f.get(url)
        for j in data.get("data", []):
            out.append(P(source="arbeitnow", slug="arbeitnow", company=j.get("company_name", ""), title=j.get("title", ""),
                         location=j.get("location", ""), remote=j.get("remote"), url=j.get("url", ""),
                         posted=parse_date(j.get("created_at")), date_field="created_at",
                         description=strip_html(j.get("description", "")),
                         employment=", ".join(j.get("job_types") or [])))
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return out


def src_himalayas(f, _=None):
    """One page of the newest jobs (offset paging is deprecated upstream)."""
    data = f.get("https://himalayas.app/jobs/api?limit=100")
    out = []
    for j in data.get("jobs", []):
        locs = j.get("locationRestrictions") or []
        comp = f"{j.get('currency','')} {j.get('minSalary')}-{j.get('maxSalary')}" if j.get("minSalary") else ""
        posted = parse_date(j.get("pubDate")) or parse_date(j.get("updatedAt"))
        out.append(P(source="himalayas", slug="himalayas", company=j.get("companyName", ""), title=j.get("title", ""),
                     location=", ".join(locs) if locs else "Worldwide", remote=True,
                     url=j.get("applicationLink") or j.get("guid", ""), posted=posted,
                     date_field="pubDate" if j.get("pubDate") else "updatedAt",
                     date_note="" if j.get("pubDate") else "updatedAt may be newer than the post date",
                     description=strip_html(j.get("description", "") or j.get("excerpt", "")),
                     comp=comp, employment=j.get("employmentType", "")))
    return out


def src_hn(f, _=None):
    """Latest 'Ask HN: Who is hiring?' thread; each top-level comment is a posting."""
    s = f.get("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=5")
    story = next((h for h in s.get("hits", []) if "who is hiring" in h.get("title", "").lower()), None)
    if not story:
        return []
    c = f.get(f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story['objectID']}"
              f"&hitsPerPage=1000")
    out = []
    for h in c.get("hits", []):
        if str(h.get("parent_id")) != str(story["objectID"]):
            continue  # replies, not postings
        text = strip_html(h.get("comment_text", ""))
        first = text.split(" | ")
        out.append(P(source="hn", slug="hn", company=first[0][:60], title=text[:160],
                     location=" | ".join(first[1:4])[:160], url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                     posted=parse_date(h.get("created_at")), date_field="created_at", description=text,
                     date_note="HN comment; title/location parsed heuristically"))
    return out


ATS = {
    "greenhouse": lambda f, s: src_greenhouse(f, s),
    "greenhouse_eu": lambda f, s: src_greenhouse(f, s, eu=True),
    "ashby": src_ashby,
    "lever": lambda f, s: src_lever(f, s),
    "lever_eu": lambda f, s: src_lever(f, s, eu=True),
    "workable": src_workable,
    "recruitee": src_recruitee,
    "teamtailor": src_teamtailor,
}
BOARDS = {"remotive": src_remotive, "remoteok": src_remoteok, "weworkremotely": src_wwr,
          "arbeitnow": src_arbeitnow, "himalayas": src_himalayas, "hn": src_hn}



def fetch_all(companies, sources=None, max_slugs=0, fixtures=None, workers=12):
    """Return (postings, status). sources: set of ATS kinds/board names, or None for all."""
    f = Fetcher(fixtures=fixtures)
    tasks = []
    for kind, fn in ATS.items():
        if sources and kind not in sources:
            continue
        for s in companies.get(kind, [])[: max_slugs or None]:
            tasks.append((f"{kind}:{s}", fn, s))
    for name, fn in BOARDS.items():
        if not sources or name in sources:
            tasks.append((name, fn, None))
    status, postings = {}, []
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn, f, s): key for key, fn, s in tasks}
        for fu in cf.as_completed(futs):
            key = futs[fu]
            try:
                r = fu.result()
                status[key] = {"ok": True, "count": len(r)}
                postings += r
            except urllib.error.HTTPError as e:
                status[key] = {"ok": False, "error": f"HTTP {e.code}", "dead_slug": e.code == 404}
            except Exception as e:  # network/proxy/parse
                status[key] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    return postings, status


def finalize(postings, now=None):
    """Add id and age_hours, drop exact duplicates (same id and URL), and sort newest
    first. Postings with no usable date go last. No age cutoff: that is a filter."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out, seen = [], set()
    for p in postings:
        p["id"] = make_id(p["company"], p["title"])
        key = (p["id"], norm_url(p["url"]))
        if key in seen:
            continue
        seen.add(key)
        d = parse_date(p["posted_at"])
        p["age_hours"] = round((now - d).total_seconds() / 3600, 1) if d else None
        out.append(p)
    out.sort(key=lambda p: (p["age_hours"] is None, p["age_hours"] or 0))
    return out


ATS_LINK = re.compile(r"(?:job-boards|boards)(\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)|jobs\.ashbyhq\.com/([\w.%-]+)|jobs(\.eu)?\.lever\.co/([\w.-]+)|apply\.workable\.com/([\w-]+)", re.I)


def discover_slugs(postings, companies):
    """Find ATS slugs in aggregator links/descriptions and add unknown ones."""
    added = []
    for p in postings:
        for m in ATS_LINK.finditer(p.get("url", "") + " " + p.get("description", "")[:20000]):
            gh_eu, gh, ash, lv_eu, lv, wk = m.groups()
            for kind, slug in (("greenhouse_eu" if gh_eu else "greenhouse", gh), ("ashby", ash),
                               ("lever_eu" if lv_eu else "lever", lv), ("workable", wk)):
                if not slug or slug.lower() in ("jobs", "embed", "api", "v1"):
                    continue
                slug = slug if kind == "ashby" else slug.lower()
                lst = companies.setdefault(kind, [])
                if slug not in lst and slug.lower() not in {s.lower() for s in lst}:
                    lst.append(slug)
                    added.append(f"{kind}:{slug}")
    for k in companies:
        companies[k].sort(key=str.lower)
    return added


