"""Source adapters: fetch every posting from job boards' own public feeds and
normalize it to one shape. No filtering happens here beyond the time window.

Normalized posting fields:
  id, source, slug, external_id, company, title, location, remote (bool|None), url,
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
    kw["listing_url"] = kw.get("listing_url") or kw.get("url", "")
    eid = kw.get("external_id")
    kw["external_id"] = "" if eid in (None, "") else str(eid)
    d = kw.get("posted")
    kw["posted_at"] = d.isoformat() if d else None
    kw.pop("posted", None)
    return kw


def src_greenhouse(f, slug):
    # Greenhouse's "EU-hosted" distinction is only a thing for the public job-board
    # viewer domain (job-boards.eu.greenhouse.io); boards-api.eu.greenhouse.io does
    # not exist (confirmed: NXDOMAIN on a real runner, while every one of those same
    # slugs resolves fine on the plain API host below) -- there's only one API host.
    data = f.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        fp = parse_date(j.get("first_published"))
        up = parse_date(j.get("updated_at"))
        posted, field, note = (fp, "first_published", "") if fp else (up, "updated_at", "updated_at changes on edits; real post date may be older")
        out.append(P(source="greenhouse", slug=slug, external_id=j.get("id"), company=j.get("company_name") or slug,
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
        out.append(P(source="ashby", slug=slug, external_id=j.get("id"), company=slug, title=j.get("title", ""),
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
        out.append(P(source="lever", slug=slug, external_id=j.get("id"), company=slug, title=j.get("text", ""),
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
        out.append(P(source="workable", slug=slug, external_id=j.get("shortcode"), company=data.get("name") or slug, title=j.get("title", ""),
                     location=loc, remote=j.get("telecommuting"), url=j.get("url") or j.get("application_url", ""),
                     posted=parse_date(j.get("published_on") or j.get("created_at")), date_field="published_on",
                     description=strip_html(j.get("description", "")), employment=j.get("employment_type", "")))
    return out


def src_recruitee(f, slug):
    data = f.get(f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        out.append(P(source="recruitee", slug=slug, external_id=j.get("id"), company=j.get("company_name") or slug, title=j.get("title", ""),
                     location=j.get("location", ""), remote=j.get("remote"), url=j.get("careers_url", ""),
                     posted=parse_date(j.get("published_at") or j.get("created_at")), date_field="published_at",
                     description=strip_html((j.get("description") or "") + " " + (j.get("requirements") or "")),
                     employment=j.get("employment_type_code", "")))
    return out


def src_teamtailor(f, slug):
    xml = f.get(f"https://{slug}.teamtailor.com/jobs.rss", kind="xml")
    return _rss(xml, "teamtailor", slug, company=slug)


def src_smartrecruiters(f, slug):
    """Public, keyless Postings API meant for external job-board syndication. The list
    endpoint has no full description (only /postings/{id} does, one call per job), so
    these postings carry a title/location snippet only -- flagged via date_note."""
    out = []
    url = f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(slug)}/postings?limit=100"
    for _page in range(3):
        data = f.get(url)
        for j in data.get("content", []):
            loc = j.get("location") or {}
            location = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            out.append(P(source="smartrecruiters", slug=slug, external_id=j.get("id"),
                         company=(j.get("company") or {}).get("name") or slug, title=j.get("name", ""),
                         location=location, remote=loc.get("remote"),
                         url=j.get("postingUrl") or j.get("applyUrl", ""),
                         posted=parse_date(j.get("releasedDate")), date_field="releasedDate",
                         employment=(j.get("typeOfEmployment") or {}).get("label", ""),
                         date_note="SmartRecruiters' list endpoint has no full description"))
        total, offset, limit = data.get("totalFound", 0), data.get("offset", 0), data.get("limit", 100)
        if offset + limit >= total:
            break
        url = f"https://api.smartrecruiters.com/v1/companies/{urllib.parse.quote(slug)}/postings?limit={limit}&offset={offset + limit}"
    return out


def src_bamboohr(f, slug):
    """Public JSON behind BambooHR's own hosted careers-page widget. No reliable posted
    date at this list level (undated postings sort last; see finalize())."""
    data = f.get(f"https://{urllib.parse.quote(slug)}.bamboohr.com/careers/list")
    out = []
    for j in data.get("result", []):
        jid = j.get("id")
        out.append(P(source="bamboohr", slug=slug, external_id=jid, company=slug,
                     title=j.get("jobOpeningName", ""), location=j.get("locationLabel", ""),
                     remote=j.get("isRemote"), url=f"https://{slug}.bamboohr.com/careers/{jid}" if jid else "",
                     employment=j.get("employmentStatusLabel", ""),
                     date_note="BambooHR's list endpoint has no posted-date field"))
    return out


def src_personio(f, slug):
    """Public XML job feed Personio exposes for every tenant's careers page."""
    xml = f.get(f"https://{urllib.parse.quote(slug)}.jobs.personio.de/xml", kind="xml")
    root = ET.fromstring(xml)
    out = []
    for pos in root.findall("position"):
        g = lambda t: (pos.findtext(t) or "").strip()  # noqa: E731
        pid = g("id")
        desc = " ".join((d.findtext("jobDescriptionValue") or "") for d in pos.findall(".//jobDescription"))
        out.append(P(source="personio", slug=slug, external_id=pid, company=slug,
                     title=g("name"), location=g("office"),
                     url=f"https://{slug}.jobs.personio.de/job/{pid}" if pid else "",
                     posted=parse_date(g("createdAt")), date_field="createdAt",
                     employment=g("employmentType"), description=strip_html(desc)))
    return out


def src_breezy(f, slug):
    """Public JSON Breezy HR exposes for every tenant's hosted job board."""
    data = f.get(f"https://{urllib.parse.quote(slug)}.breezy.hr/json")
    out = []
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        jid = j.get("id") or j.get("friendly_id")
        out.append(P(source="breezy", slug=slug, external_id=jid, company=slug, title=j.get("name", ""),
                     location=loc.get("name", ""), remote=loc.get("is_remote"),
                     url=f"https://{slug}.breezy.hr/p/{jid}" if jid else "",
                     posted=parse_date(j.get("published_date")), date_field="published_date",
                     employment=(j.get("type") or {}).get("name", ""), description=strip_html(j.get("description", ""))))
    return out


def _rss(xml, source, slug, company=None):
    out = []
    root = ET.fromstring(xml)
    for it in root.iter("item"):
        g = lambda t: (it.findtext(t) or "").strip()
        title = g("title")
        comp_name = company
        if source == "weworkremotely" and ":" in title:  # "Company: Role"
            comp_name, title = [x.strip() for x in title.split(":", 1)]
        out.append(P(source=source, slug=slug, external_id=g("guid") or g("link"), company=comp_name or "", title=title,
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
    return [P(source="remotive", slug="remotive", external_id=j.get("id"), company=j.get("company_name", ""), title=j.get("title", ""),
              location=j.get("candidate_required_location", ""), remote=True, url=j.get("url", ""),
              posted=parse_date(j.get("publication_date")), date_field="publication_date",
              description=strip_html(j.get("description", "")), comp=j.get("salary", ""),
              employment=j.get("job_type", "")) for j in data.get("jobs", [])]


def src_remoteok(f, _=None):
    data = f.get("https://remoteok.com/api")
    out = []
    for j in data[1:] if isinstance(data, list) else []:  # first item is legal notice
        comp = f"${j['salary_min']}-{j['salary_max']}" if j.get("salary_min") else ""
        out.append(P(source="remoteok", slug="remoteok", external_id=j.get("id"), company=j.get("company", ""), title=j.get("position", ""),
                     location=j.get("location", ""), remote=True, url=j.get("apply_url") or j.get("url", ""), listing_url=j.get("url", ""),
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
            out.append(P(source="arbeitnow", slug="arbeitnow", external_id=j.get("slug"), company=j.get("company_name", ""), title=j.get("title", ""),
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
        out.append(P(source="himalayas", slug="himalayas", external_id=j.get("guid"), company=j.get("companyName", ""), title=j.get("title", ""),
                     location=", ".join(locs) if locs else "Worldwide", remote=True,
                     url=j.get("applicationLink") or j.get("guid", ""), listing_url=j.get("guid", ""), posted=posted,
                     date_field="pubDate" if j.get("pubDate") else "updatedAt",
                     date_note="" if j.get("pubDate") else "updatedAt may be newer than the post date",
                     description=strip_html(j.get("description", "") or j.get("excerpt", "")),
                     comp=comp, employment=j.get("employmentType", "")))
    return out


def src_jobicy(f, _=None):
    """Jobicy publishes this API specifically for external aggregation (documented,
    no key needed) -- same posture as Remotive/RemoteOK, which are already in BOARDS."""
    data = f.get("https://jobicy.com/api/v2/remote-jobs?count=50")
    out = []
    for j in data.get("jobs", []):
        comp = (f"{j.get('salaryCurrency', '')} {j.get('annualSalaryMin', '')}-{j.get('annualSalaryMax', '')}".strip()
                if j.get("annualSalaryMin") else "")
        out.append(P(source="jobicy", slug="jobicy", external_id=j.get("id"), company=j.get("companyName", ""),
                     title=j.get("jobTitle", ""), location=j.get("jobGeo", ""), remote=True,
                     url=j.get("url", ""), posted=parse_date(j.get("pubDate")), date_field="pubDate",
                     description=strip_html(j.get("jobDescription") or j.get("jobExcerpt", "")),
                     comp=comp, employment=", ".join(j.get("jobType") or [])))
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
        # Convention: "Company | Role(s) | Location | Type | ..." on the first line
        seg = [x.strip() for x in text[:400].split("|")]
        out.append(P(source="hn", slug="hn", external_id=h["objectID"], company=seg[0][:60],
                     title=(seg[1] if len(seg) > 1 else text)[:160],
                     location=(" | ".join(seg[2:4]) if len(seg) > 2 else "")[:160], url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                     posted=parse_date(h.get("created_at")), date_field="created_at", description=text,
                     date_note="HN comment; title/location parsed heuristically"))
    return out


ATS = {
    "greenhouse": lambda f, s: src_greenhouse(f, s),
    "ashby": src_ashby,
    "lever": lambda f, s: src_lever(f, s),
    "lever_eu": lambda f, s: src_lever(f, s, eu=True),
    "workable": src_workable,
    "recruitee": src_recruitee,
    "teamtailor": src_teamtailor,
    "smartrecruiters": src_smartrecruiters,
    "bamboohr": src_bamboohr,
    "personio": src_personio,
    "breezy": src_breezy,
}
BOARDS = {"remotive": src_remotive, "remoteok": src_remoteok, "weworkremotely": src_wwr,
          "arbeitnow": src_arbeitnow, "himalayas": src_himalayas, "hn": src_hn, "jobicy": src_jobicy}



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


ATS_LINK = re.compile(
    r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)"
    r"|jobs\.ashbyhq\.com/([\w.%-]+)"
    r"|jobs(\.eu)?\.lever\.co/([\w.-]+)"
    r"|apply\.workable\.com/([\w-]+)"
    r"|jobs\.smartrecruiters\.com/([\w.-]+)"
    r"|([\w-]+)\.bamboohr\.com"
    r"|([\w-]+)\.jobs\.personio\.(?:de|com)"
    r"|([\w-]+)\.breezy\.hr",
    re.I,
)


def discover_slugs(postings, companies):
    """Find ATS slugs in aggregator links/descriptions and add unknown ones."""
    added = []
    for p in postings:
        for m in ATS_LINK.finditer(p.get("url", "") + " " + p.get("description", "")[:20000]):
            gh, ash, lv_eu, lv, wk, sr, bh, pe, bz = m.groups()
            for kind, slug in (("greenhouse", gh), ("ashby", ash),
                               ("lever_eu" if lv_eu else "lever", lv), ("workable", wk),
                               ("smartrecruiters", sr), ("bamboohr", bh), ("personio", pe), ("breezy", bz)):
                if not slug or slug.lower() in ("jobs", "embed", "api", "v1", "www"):
                    continue
                slug = slug if kind == "ashby" else slug.lower()
                lst = companies.setdefault(kind, [])
                if slug not in lst and slug.lower() not in {s.lower() for s in lst}:
                    lst.append(slug)
                    added.append(f"{kind}:{slug}")
    for k in companies:
        companies[k].sort(key=str.lower)
    return added


