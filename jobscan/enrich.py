"""Turn a normalized posting into the compact row stored in ApplyX's shared
`job_postings` table.

The full description is read here and then dropped: only the tags that user filters
need, plus a short snippet, are kept. Nothing in this module is specific to one user
or one kind of role.

    row = to_row(posting)   # dict ready for the upsert_postings RPC
"""
import hashlib
import json
import re
import urllib.parse

from .filters import required_years

SNIPPET_CHARS = 300
DESC_SCAN_CHARS = 6000

# --------------------------------------------------------------------------
# URL normalization: a port of normalizeJobUrl in ApplyX's
# chrome-extension/src/background/index.ts. Keep the two in sync, so a job saved
# by the extension and the same job in the feed produce the same string.
# --------------------------------------------------------------------------

JOB_ID_PARAMS_BY_HOST = {
    "www.linkedin.com": "currentJobId",
    "boards.greenhouse.io": "gh_jid",
    "job-boards.greenhouse.io": "gh_jid",
    "job-boards.eu.greenhouse.io": "gh_jid",
}


def normalize_job_pathname(path):
    path = re.sub(r"/application/?$", "", path, flags=re.I)
    path = re.sub(r"/+$", "", path)
    return path or "/"


def normalize_job_url(url):
    """Drop the hash and every query param except the host's job-id param, and strip a
    trailing slash or /application from the path. Mirrors the extension's output."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return u
    if not p.scheme or not p.hostname:
        return u
    host = p.hostname.lower()
    try:
        port = p.port
    except ValueError:
        return u
    default = {"http": 80, "https": 443}.get(p.scheme.lower())
    netloc = host + (f":{port}" if port and port != default else "")
    query = ""
    param = JOB_ID_PARAMS_BY_HOST.get(host)
    if param:
        vals = urllib.parse.parse_qs(p.query).get(param)
        if vals and vals[0]:
            query = "?" + urllib.parse.urlencode({param: vals[0]})
    return f"{p.scheme.lower()}://{netloc}{normalize_job_pathname(p.path or '/')}{query}"


# --------------------------------------------------------------------------
# classifiers
# --------------------------------------------------------------------------

def _rx(*words):
    return re.compile("|".join(f"(?:{w})" for w in words), re.I)


# Title -> role families. A title can get several (e.g. "Full-stack / Mobile").
ROLE_FAMILIES = [
    ("frontend", _rx(r"front[\s-]?end", r"\bui (engineer|developer)", r"\bweb (engineer|developer)", r"\breact\b(?! native)", r"\bvue", r"angular", r"svelte", r"javascript (engineer|developer)")),
    ("fullstack", _rx(r"full[\s-]?stack")),
    ("backend", _rx(r"back[\s-]?end", r"\bapi (engineer|developer)", r"\b(java|golang|go|python|ruby|rails|php|node(\.?js)?|\.net|c#|scala|elixir) (engineer|developer)")),
    ("mobile", _rx(r"\bmobile\b", r"\bios\b", r"android", r"react native", r"flutter", r"\bswift\b", r"kotlin")),
    ("data_engineering", _rx(r"data (engineer|platform|infrastructure)", r"analytics engineer", r"\betl\b")),
    ("data_science", _rx(r"data scien", r"machine learning", r"\bml\b", r"\bai\b", r"deep learning", r"\bnlp\b", r"computer vision", r"research (scientist|engineer)", r"\bllm")),
    ("data_analysis", _rx(r"\bdata analyst", r"business intelligence", r"\bbi (analyst|developer)", r"analytics (analyst|manager)")),
    ("devops", _rx(r"devops", r"\bsre\b", r"site reliability", r"platform engineer", r"infrastructure", r"cloud (engineer|architect)", r"\bdevsecops")),
    ("security", _rx(r"security", r"\bsecops\b", r"penetration", r"\bappsec\b")),
    ("qa", _rx(r"\bqa\b", r"quality (assurance|engineer)", r"\btest(ing)? (engineer|automation)", r"\bsdet\b")),
    ("embedded", _rx(r"embedded", r"firmware", r"hardware", r"\bfpga\b", r"electrical engineer")),
    ("engineering_management", _rx(r"engineering manager", r"manager,? (software )?engineering", r"head of engineering", r"\bvp,? engineering", r"\bcto\b", r"director,? (of )?engineering")),
    ("product", _rx(r"product manager", r"product owner", r"\bhead of product", r"product lead", r"\bpm\b")),
    ("design", _rx(r"designer", r"\bux\b", r"\bui/ux\b", r"design (lead|manager)", r"user research")),
    ("sales", _rx(r"\bsales", r"account (executive|manager)", r"business development", r"\bsdr\b", r"\bbdr\b", r"solutions? (engineer|consultant)", r"pre[\s-]?sales")),
    ("marketing", _rx(r"marketing", r"\bseo\b", r"growth", r"content (writer|strategist|manager)", r"copywriter", r"brand", r"communications")),
    ("customer_success", _rx(r"customer (success|support|service|experience)", r"\bsupport (engineer|specialist|agent)", r"technical support", r"account manager")),
    ("operations", _rx(r"operations", r"\bops\b", r"supply chain", r"logistics", r"project manager", r"program manager", r"office manager")),
    ("finance", _rx(r"financ", r"accountant", r"accounting", r"controller", r"\bfp&a\b", r"payroll", r"\btax\b")),
    ("people", _rx(r"recruit", r"talent", r"\bhr\b", r"human resources", r"people (partner|operations)")),
    ("legal", _rx(r"legal", r"counsel", r"lawyer", r"compliance", r"paralegal")),
]
GENERIC_SOFTWARE = _rx(r"software (engineer|developer)", r"\bswe\b", r"\bdeveloper\b", r"\bprogrammer\b", r"product engineer", r"applications? engineer")

SENIORITY = [  # first match wins, most specific first
    ("executive", _rx(r"\b(vp|vice president|chief|cto|ceo|cfo|coo|cpo)\b")),
    ("manager", _rx(r"\b(head of|director)\b", r"engineering manager", r"manager,? (software )?engineering", r"\bmanager of\b")),
    ("staff_plus", _rx(r"\b(staff|principal|distinguished|fellow|architect)\b")),
    ("lead", _rx(r"\b(lead|tech lead|team lead)\b")),
    ("intern", _rx(r"\b(intern|internship|werkstudent|working student|praktik)", r"\btrainee\b", r"\bapprentice")),
    ("junior", _rx(r"\b(junior|jr\.?|entry[\s-]level|graduate|new grad|associate)\b", r"\b(i|1)\b$")),
    ("senior", _rx(r"\b(senior|sr\.?)\b", r"\b(iii|3|iv|4)\b$")),
    ("mid", _rx(r"\b(mid|intermediate|ii|2)\b")),
]

TECH = {
    "react": r"\breact(\.?js)?\b(?! native)", "react_native": r"react native", "vue": r"\bvue(\.?js)?\b",
    "angular": r"\bangular\b", "svelte": r"\bsvelte", "nextjs": r"\bnext\.?js\b", "typescript": r"\btypescript\b",
    "javascript": r"\bjavascript\b", "nodejs": r"\bnode(\.?js)?\b", "python": r"\bpython\b", "django": r"\bdjango\b",
    "fastapi": r"\bfastapi\b", "flask": r"\bflask\b", "java": r"\bjava\b(?!script)", "kotlin": r"\bkotlin\b",
    "spring": r"\bspring( boot)?\b", "go": r"\bgolang\b|\bgo (developer|engineer|services)\b", "rust": r"\brust\b",
    "ruby": r"\bruby\b", "rails": r"\brails\b", "php": r"\bphp\b", "laravel": r"\blaravel\b", "symfony": r"\bsymfony\b",
    "wordpress": r"\bwordpress\b", "dotnet": r"\.net\b|\bc#|asp\.net|\bblazor\b", "cpp": r"\bc\+\+", "swift": r"\bswift\b",
    "flutter": r"\bflutter\b", "scala": r"\bscala\b", "elixir": r"\belixir\b", "graphql": r"\bgraphql\b",
    "postgres": r"\bpostgres(ql)?\b", "mysql": r"\bmysql\b", "mongodb": r"\bmongo(db)?\b", "redis": r"\bredis\b",
    "aws": r"\baws\b|amazon web services", "gcp": r"\bgcp\b|google cloud", "azure": r"\bazure\b",
    "kubernetes": r"\bkubernetes\b|\bk8s\b", "docker": r"\bdocker\b", "terraform": r"\bterraform\b",
    "tailwind": r"\btailwind", "sql": r"\bsql\b", "spark": r"\bspark\b", "pytorch": r"\bpytorch\b",
    "tensorflow": r"\btensorflow\b", "salesforce": r"\bsalesforce\b", "sap": r"\bsap\b",
}
TECH_RX = {k: re.compile(v, re.I) for k, v in TECH.items()}
TECH_PRIMARY_MIN_HITS = 3

EMPLOYMENT = [
    ("internship", _rx(r"intern", r"werkstudent", r"working student", r"praktik")),
    ("contract", _rx(r"contract", r"freelance", r"contractor", r"\bb2b\b")),
    ("part_time", _rx(r"part[\s_-]?time", r"minijob")),
    ("temporary", _rx(r"temporary", r"\btemp\b", r"fixed[\s-]term")),
    ("full_time", _rx(r"full[\s_-]?time", r"permanent", r"\bfte\b", r"festanstellung")),
]

REMOTE_RX = _rx(r"remote", r"distributed", r"home[\s-]?office", r"work from home", r"\bwfh\b", r"anywhere")
HYBRID_RX = _rx(r"hybrid")
ONSITE_RX = _rx(r"on[\s-]?site", r"in[\s-]office", r"office[\s-]based")
WORLDWIDE_RX = _rx(r"anywhere", r"worldwide", r"world[\s-]?wide", r"\bglobal(ly)?\b", r"work from anywhere",
                   r"all countries", r"no (location|geo(graphic)?) restriction", r"^remote$")

REGIONS = {
    "europe": _rx(r"europe", r"\beu\b", r"european", r"\bcet\b", r"\bcest\b", r"\beea\b"),
    "emea": _rx(r"\bemea\b"),
    "asia": _rx(r"\basia\b", r"\bapac\b", r"asia[\s-]pacific", r"eastern hemisphere"),
    "americas": _rx(r"americas", r"\blatam\b", r"latin america", r"south america", r"north america", r"\bamer\b"),
    "north_america": _rx(r"north america", r"\bus/canada\b", r"\bus (and|&) canada\b"),
    "latam": _rx(r"\blatam\b", r"latin america", r"south america"),
    "africa": _rx(r"\bafrica\b"),
    "middle_east": _rx(r"middle east", r"\bmena\b", r"\bgcc\b"),
    "oceania": _rx(r"oceania", r"\banz\b"),
}

COUNTRIES = {
    "us": ["us", "usa", r"u\.s\.a?\.?", "united states", "america"], "ca": ["canada"],
    "gb": ["uk", r"u\.k\.", "united kingdom", "england", "scotland", "wales", "great britain"],
    "ie": ["ireland"], "fr": ["france"], "es": ["spain", "españa"], "pt": ["portugal"], "it": ["italy", "italia"],
    "nl": ["netherlands", "holland"], "be": ["belgium"], "de": ["germany", "deutschland"], "at": ["austria", "österreich"],
    "ch": ["switzerland", "schweiz"], "pl": ["poland", "polska"], "cz": ["czech"], "sk": ["slovakia"], "hu": ["hungary"],
    "ro": ["romania"], "bg": ["bulgaria"], "gr": ["greece"], "rs": ["serbia"], "hr": ["croatia"], "si": ["slovenia"],
    "ee": ["estonia"], "lv": ["latvia"], "lt": ["lithuania"], "fi": ["finland"], "se": ["sweden"], "no": ["norway"],
    "dk": ["denmark"], "is": ["iceland"], "ua": ["ukraine"], "tr": ["turkey", "türkiye"], "cy": ["cyprus"], "mt": ["malta"],
    "lu": ["luxembourg"], "il": ["israel"], "ae": ["uae", "united arab emirates", "dubai", "abu dhabi"],
    "sa": ["saudi"], "eg": ["egypt"], "ng": ["nigeria"], "ke": ["kenya"], "za": ["south africa"], "ma": ["morocco"],
    "in": ["india"], "pk": ["pakistan"], "bd": ["bangladesh"], "lk": ["sri lanka"], "sg": ["singapore"],
    "my": ["malaysia"], "id": ["indonesia"], "th": ["thailand"], "vn": ["vietnam"], "ph": ["philippines"],
    "jp": ["japan"], "kr": ["korea"], "cn": ["china"], "hk": ["hong kong"], "tw": ["taiwan"],
    "au": ["australia"], "nz": ["new zealand"], "br": ["brazil", "brasil"], "mx": ["mexico", "méxico"],
    "ar": ["argentina"], "co": ["colombia"], "cl": ["chile"], "pe": ["peru"], "uy": ["uruguay"], "cr": ["costa rica"],
}
COUNTRY_RX = {code: re.compile("|".join(rf"\b{w}\b" for w in words), re.I) for code, words in COUNTRIES.items()}

CITIES = {  # city -> country code
    "berlin": "de", "munich": "de", "münchen": "de", "hamburg": "de", "cologne": "de", "köln": "de", "frankfurt": "de",
    "stuttgart": "de", "düsseldorf": "de", "leipzig": "de", "london": "gb", "manchester": "gb", "edinburgh": "gb",
    "dublin": "ie", "paris": "fr", "lyon": "fr", "amsterdam": "nl", "rotterdam": "nl", "brussels": "be",
    "madrid": "es", "barcelona": "es", "valencia": "es", "lisbon": "pt", "porto": "pt", "milan": "it", "rome": "it",
    "zurich": "ch", "zürich": "ch", "geneva": "ch", "vienna": "at", "wien": "at", "warsaw": "pl", "krakow": "pl",
    "kraków": "pl", "wroclaw": "pl", "prague": "cz", "budapest": "hu", "bucharest": "ro", "sofia": "bg",
    "athens": "gr", "belgrade": "rs", "zagreb": "hr", "tallinn": "ee", "riga": "lv", "vilnius": "lt",
    "helsinki": "fi", "stockholm": "se", "oslo": "no", "copenhagen": "dk", "kyiv": "ua", "istanbul": "tr",
    "tel aviv": "il", "dubai": "ae", "new york": "us", "nyc": "us", "san francisco": "us", "seattle": "us",
    "austin": "us", "boston": "us", "chicago": "us", "los angeles": "us", "denver": "us", "atlanta": "us",
    "miami": "us", "toronto": "ca", "vancouver": "ca", "montreal": "ca", "bangalore": "in", "bengaluru": "in",
    "hyderabad": "in", "pune": "in", "mumbai": "in", "delhi": "in", "gurgaon": "in", "noida": "in", "chennai": "in",
    "karachi": "pk", "lahore": "pk", "islamabad": "pk", "singapore": "sg", "tokyo": "jp", "seoul": "kr",
    "sydney": "au", "melbourne": "au", "auckland": "nz", "são paulo": "br", "sao paulo": "br",
    "mexico city": "mx", "buenos aires": "ar", "bogota": "co", "bogotá": "co", "santiago": "cl",
}
CITY_RX = {c: re.compile(rf"\b{re.escape(c)}\b", re.I) for c in CITIES}


def role_families(title):
    fams = [name for name, rx in ROLE_FAMILIES if rx.search(title)]
    if not fams and GENERIC_SOFTWARE.search(title):
        fams.append("software")
    return fams or ["other"]


def seniority(title):
    for name, rx in SENIORITY:
        if rx.search(title):
            return name
    return "unknown"


def tech_tags(title, desc):
    text = desc[:DESC_SCAN_CHARS]
    mentioned, primary = [], []
    for tag, rx in TECH_RX.items():
        in_title = bool(rx.search(title))
        hits = len(rx.findall(text))
        if in_title or hits:
            mentioned.append(tag)
        if in_title or hits >= TECH_PRIMARY_MIN_HITS:
            primary.append(tag)
    return mentioned, primary


def employment_type(employment, title):
    for name, rx in EMPLOYMENT:
        if rx.search(employment or ""):
            return name
    for name, rx in EMPLOYMENT[:4]:  # titles rarely say "full-time"
        if rx.search(title):
            return name
    return "full_time" if employment else "unknown"


def locate(location, remote_flag, title, desc):
    """Return workplace, remote_scope, regions, countries, cities."""
    loc = location or ""
    head = f"{title} {loc}"
    regions = sorted(r for r, rx in REGIONS.items() if rx.search(loc))
    cities = sorted(c for c, rx in CITY_RX.items() if rx.search(loc))
    countries = {code for code, rx in COUNTRY_RX.items() if rx.search(loc)}
    countries |= {CITIES[c] for c in cities}
    is_remote = bool(remote_flag) or bool(REMOTE_RX.search(loc))
    if HYBRID_RX.search(head):
        workplace = "hybrid"
    elif is_remote:
        workplace = "remote"
    elif ONSITE_RX.search(head) or cities or countries:
        workplace = "onsite"
    else:
        workplace = "unknown"
    scope = None
    if workplace == "remote":
        if WORLDWIDE_RX.search(loc.strip()) and not countries and not regions:
            scope = "worldwide"
        elif regions:
            scope = "region"
        elif countries:
            scope = "country"
        elif not loc.strip() and re.search(r"anywhere in the world|work from anywhere|fully distributed", desc[:4000], re.I):
            scope = "worldwide"
        else:
            scope = "unknown"
    return workplace, scope, regions, sorted(countries), cities


def snippet(desc):
    d = re.sub(r"\s+", " ", desc or "").strip()
    if len(d) <= SNIPPET_CHARS:
        return d
    cut = d[:SNIPPET_CHARS].rsplit(" ", 1)[0]
    return cut + "…"


def external_id(p):
    eid = (p.get("external_id") or "").strip()
    if eid:
        return eid[:200]
    return "u:" + hashlib.sha1(normalize_job_url(p.get("url", "")).encode()).hexdigest()


HASHED = ("company", "title", "location", "remote", "employment", "comp_text", "snippet", "apply_url",
          "listing_url", "role_family", "seniority", "years_required", "tech", "tech_primary", "workplace",
          "remote_scope", "regions", "countries", "cities", "posted_at", "posted_at_exact")


def to_row(p):
    title, desc = p.get("title") or "", p.get("description") or ""
    mentioned, primary = tech_tags(title, desc)
    workplace, scope, regions, countries, cities = locate(p.get("location"), p.get("remote"), title, desc)
    url = p.get("url") or ""
    row = {
        "source": p["source"],
        "external_id": external_id(p),
        "board_slug": p.get("slug"),
        "company": (p.get("company") or "")[:200],
        "title": title[:300],
        "location": (p.get("location") or "")[:300] or None,
        "remote": p.get("remote"),
        "employment": employment_type(p.get("employment"), title),
        "comp_text": (p.get("comp") or "")[:200] or None,
        "snippet": snippet(desc) or None,
        "apply_url": url,
        # The source's own page, which aggregator terms (RemoteOK, Himalayas, WWR) ask us
        # to link back to. For ATS boards it is the same as apply_url.
        "listing_url": p.get("listing_url") or url,
        "canonical_url": normalize_job_url(url),
        "role_family": role_families(title),
        "seniority": seniority(title),
        "years_required": required_years(desc),
        "tech": mentioned,
        "tech_primary": primary,
        "workplace": workplace,
        "remote_scope": scope,
        "regions": regions,
        "countries": countries,
        "cities": cities,
        "posted_at": p.get("posted_at"),
        "posted_at_field": p.get("date_field"),
        "posted_at_exact": p.get("date_field") not in ("updated_at", "updatedAt"),
    }
    row["content_hash"] = hashlib.sha1(json.dumps([row[k] for k in HASHED], ensure_ascii=False,
                                                  default=str).encode()).hexdigest()
    return row


__all__ = ["to_row", "normalize_job_url", "role_families", "seniority", "tech_tags", "locate", "employment_type"]
