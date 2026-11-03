#!/usr/bin/env python3
"""Daily fresh-jobs scanner.

Reads job boards' own public feeds (Greenhouse, Ashby, Lever, Workable,
Recruitee, Teamtailor, Remotive, RemoteOK, We Work Remotely, Arbeitnow,
Himalayas, HN "Who is hiring"), keeps postings published in the last N hours,
filters them against the criteria, dedupes against data/job-db.json and writes
a candidates JSON for review.

Stdlib only. Run from the repo root:
    python3 -I scripts/fetch_jobs.py                # full run
    python3 -I scripts/fetch_jobs.py --sources greenhouse,ashby --max-slugs 5
    python3 -I scripts/fetch_jobs.py --fixtures tests/fixtures   # offline

Exit codes: 0 ok, 2 zero sources reachable.
"""
import argparse
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "fresh-jobs-scanner/1.0 (personal job search; stdlib urllib)"
NOW = dt.datetime.now(dt.timezone.utc)

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
    since = int((NOW - dt.timedelta(days=3)).timestamp())
    c = f.get(f"https://hn.algolia.com/api/v1/search_by_date?tags=comment,story_{story['objectID']}"
              f"&numericFilters=created_at_i>{since}&hitsPerPage=1000")
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

# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------

TITLE_YES = re.compile(r"front[\s-]?end|full[\s-]?stack|\breact\b|typescript|\bweb\b|\bui engineer|javascript|next\.?js|vue|angular|product engineer", re.I)
TITLE_GENERIC_SWE = re.compile(r"software (engineer|developer)|\bswe\b|developer|engineer", re.I)
TITLE_NO = re.compile(r"back[\s-]?end only|\bdata (engineer|scientist)|machine learning|\bml\b|devops|\bsre\b|site reliability|security|\bqa\b|test|android|\bios\b|embedded|firmware|manager|designer|recruit|sales|marketing manager|support|analyst|intern\b|working student|werkstudent|praktik", re.I)
SENIOR_NO = re.compile(r"\b(staff|principal|lead|head|director|vp|vice president|architect|distinguished|chief|cto)\b|tech lead|team lead", re.I)
JUNIOR_NO = re.compile(r"\b(junior|jr\.?|entry[\s-]level|graduate|new grad|intern|trainee|apprentice)\b", re.I)
EMPLOY_NO = re.compile(r"contract|freelance|part[\s-]?time|temporary|internship|werkstudent|minijob", re.I)
TECH_BAD = re.compile(r"\.net\b|\bc#|asp\.net|\bphp\b|laravel|symfony|wordpress|magento|drupal|blazor", re.I)
TECH_GOOD = re.compile(r"\breact\b|typescript|next\.?js|vue|angular|svelte|node\.?js|javascript", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?", re.I)

WORLDWIDE = re.compile(r"anywhere|worldwide|world[\s-]?wide|global(ly)?\b|remote\s*\(?any(where)?|work from anywhere|all countries|no (location|geo(graphic)?) restriction", re.I)
REGION = re.compile(r"europe|\beu\b|emea|\bcet\b|cest|european|asia|apac|eastern hemisphere|eu timezone|\buk or eu\b", re.I)
GERMANY = re.compile(r"germany|deutschland|\bde\b", re.I)
BERLIN = re.compile(r"berlin", re.I)
REMOTE_WORD = re.compile(r"remote|distributed|home[\s-]?office|work from home|wfh", re.I)
AMERICAS = re.compile(r"americas|latam|latin america|north america|south america", re.I)
COUNTRY_LOCK = re.compile(
    r"\b(us|usa|u\.s\.a?\.?|united states|canada|uk|u\.k\.|united kingdom|england|ireland|france|spain|portugal|"
    r"italy|netherlands|poland|romania|brazil|brasil|mexico|argentina|colombia|chile|india|pakistan|philippines|"
    r"australia|new zealand|singapore|japan|israel|turkey|ukraine|sweden|denmark|norway|finland|switzerland|austria|"
    r"belgium|czech|hungary|greece|bulgaria|serbia|croatia|estonia|latvia|lithuania|nigeria|kenya|south africa|egypt|"
    r"uae|dubai|saudi|vietnam|indonesia|malaysia|thailand|korea|china|hong kong|taiwan)\b", re.I)
US_CITY = re.compile(r"\b(new york|nyc|san francisco|sf|seattle|austin|boston|chicago|los angeles|denver|toronto|london|paris|amsterdam|madrid|barcelona|lisbon|dublin|warsaw|stockholm|munich|hamburg|cologne|frankfurt|zurich|vienna|tel aviv|bangalore|bengaluru)\b", re.I)


def classify_location(loc, remote, desc):
    """Return (bucket, priority, reason). bucket in ok|review|exclude.
    priority: 1 worldwide remote, 2 region / Germany remote, 3 Berlin onsite/hybrid."""
    l = loc or ""
    is_remote = bool(remote) or bool(REMOTE_WORD.search(l))
    parts = [p.strip() for p in re.split(r"\s*\|\s*|;\s*", l) if p.strip()]
    if BERLIN.search(l):
        return "ok", (2 if is_remote else 3), "Berlin" + (" (remote ok)" if is_remote else "")
    if WORLDWIDE.search(l):
        return "ok", 1, "worldwide remote"
    if AMERICAS.search(l) and not REGION.search(l):
        return "exclude", 9, "remote restricted to Americas"
    if is_remote and GERMANY.search(l) and not COUNTRY_LOCK.search(GERMANY.sub("", l)):
        return "ok", 2, "remote Germany"
    if REGION.search(l):
        return "ok", 2, "broad-region remote"
    if GERMANY.search(l) and not is_remote:
        return "exclude", 9, f"onsite in Germany outside Berlin ({l[:40]})"
    countries = {m.group(0).lower() for m in COUNTRY_LOCK.finditer(l)}
    if countries:
        if len(countries) == 1:
            return "exclude", 9, f"single-country ({next(iter(countries)).upper()})"
        return "exclude", 9, f"country list ({', '.join(sorted(countries))})"
    if US_CITY.search(l) and not is_remote:
        return "exclude", 9, f"onsite outside Berlin ({l[:40]})"
    if US_CITY.search(l):
        return "review", 5, f"remote tied to a city ({l[:60]})"
    if is_remote or not l:
        # "Remote" with no scope: check description for clues
        d = desc[:4000]
        if WORLDWIDE.search(d):
            return "review", 4, "remote; description mentions worldwide"
        return "review", 5, "remote scope unstated"
    return "review", 5, f"unrecognized location '{l[:60]}'"


def required_years(desc):
    yrs = []
    for m in YEARS_RE.finditer(desc[:8000]):
        ctx = desc[max(0, m.start() - 40): m.end() + 40].lower()
        if "experience" in ctx or "professional" in ctx or "industry" in ctx:
            n = int(m.group(1))
            if 0 < n < 30:
                yrs.append(n)
    return min(yrs) if yrs else None


def evaluate(p):
    """Return (verdict, reasons, meta). verdict: candidate | review | excluded | ignore."""
    t, d = p["title"], p.get("description", "")
    if not (TITLE_YES.search(t) or (TITLE_GENERIC_SWE.search(t) and TECH_GOOD.search(d[:3000]))):
        return "ignore", ["title not frontend/fullstack"], {}
    if TITLE_NO.search(t) and not re.search(r"front|full", t, re.I):
        return "ignore", ["non-matching discipline"], {}
    reasons = []
    if SENIOR_NO.search(t):
        return "excluded", [f"too senior ({SENIOR_NO.search(t).group(0)})"], {}
    if JUNIOR_NO.search(t):
        return "ignore", ["junior level"], {}
    if EMPLOY_NO.search(p.get("employment", "")) or EMPLOY_NO.search(t):
        return "excluded", ["contract/freelance/part-time"], {}
    if re.search(r"back[\s-]?end", t, re.I) and not re.search(r"front|full", t, re.I):
        return "ignore", ["backend-only"], {}
    bad = TECH_BAD.findall(t) or []
    if bad:
        return "excluded", [f"{bad[0]} primary (title)"], {}
    bad_d, good_d = len(TECH_BAD.findall(d[:6000])), len(TECH_GOOD.findall(d[:6000]))
    if bad_d >= 3 and bad_d > good_d:
        return "excluded", [".NET/PHP primary (description)"], {}
    yrs = required_years(d)
    if yrs and yrs > 8:
        return "excluded", [f"{yrs}+ yrs required"], {"years": yrs}
    bucket, prio, why = classify_location(p.get("location", ""), p.get("remote"), d)
    meta = {"priority": prio, "location_class": why, "years": yrs}
    if bucket == "exclude":
        return "excluded", [why], meta
    if bad_d:
        reasons.append(f".NET/PHP mentioned {bad_d}x in description")
    if bucket == "review":
        return "review", reasons + [why], meta
    return "candidate", reasons, meta


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--db", default=os.path.join(ROOT, "data/job-db.json"))
    ap.add_argument("--companies", default=os.path.join(ROOT, "data/companies.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out/candidates.json"))
    ap.add_argument("--sources", default="all", help="comma list of ATS kinds / board names, or 'all'")
    ap.add_argument("--max-slugs", type=int, default=0, help="limit slugs per ATS (testing)")
    ap.add_argument("--fixtures", help="read responses from a fixtures dir instead of the network")
    ap.add_argument("--no-discover", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()

    f = Fetcher(fixtures=a.fixtures)
    with open(a.db, encoding="utf-8") as fh:
        db = json.load(fh)
    with open(a.companies, encoding="utf-8") as fh:
        companies = json.load(fh)
    known_ids = {e["id"] for e in db}
    known_urls = {norm_url(e.get("link", "")) for e in db if e.get("link")}

    want = None if a.sources == "all" else set(a.sources.split(","))
    tasks = []
    for kind, fn in ATS.items():
        if want and kind not in want:
            continue
        slugs = companies.get(kind, [])
        for s in slugs[: a.max_slugs or None]:
            tasks.append((f"{kind}:{s}", fn, s))
    for name, fn in BOARDS.items():
        if not want or name in want:
            tasks.append((name, fn, None))

    status, postings = {}, []
    with cf.ThreadPoolExecutor(a.workers) as ex:
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

    ok = [k for k, v in status.items() if v["ok"]]
    cutoff = NOW - dt.timedelta(hours=a.hours)
    res = {"candidates": [], "needs_review": [], "excluded": []}
    seen = set()
    for p in postings:
        d = parse_date(p["posted_at"])
        if not d or d < cutoff:
            continue
        p["id"] = make_id(p["company"], p["title"])
        u = norm_url(p["url"])
        if p["id"] in known_ids or (u and u in known_urls) or p["id"] in seen:
            continue
        verdict, reasons, meta = evaluate(p)
        if verdict == "ignore":
            continue
        seen.add(p["id"])
        p["age_hours"] = round((NOW - d).total_seconds() / 3600, 1)
        p["flags"] = reasons
        p.update(meta)
        p["description"] = p["description"][:1200]
        res[{"candidate": "candidates", "review": "needs_review", "excluded": "excluded"}[verdict]].append(p)

    for k in res:
        res[k].sort(key=lambda p: (p.get("priority", 9), p.get("age_hours", 999)))

    added = [] if a.no_discover else discover_slugs(postings, companies)
    if added and not a.fixtures:
        with open(a.companies, "w", encoding="utf-8") as fh:
            json.dump(companies, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    summary = {
        "run_at": NOW.isoformat(), "window_hours": a.hours,
        "sources_ok": len(ok), "sources_failed": len(status) - len(ok),
        "postings_scanned": len(postings),
        "counts": {k: len(v) for k, v in res.items()},
        "new_slugs": added,
        "dead_slugs": sorted(k for k, v in status.items() if v.get("dead_slug")),
        "failed_sources": {k: v["error"] for k, v in sorted(status.items()) if not v["ok"] and not v.get("dead_slug")},
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, **res}, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not ok:
        print("ERROR: zero sources reachable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
