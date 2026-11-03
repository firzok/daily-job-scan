"""Profile-driven filtering of normalized postings.

A profile is a JSON file (see profiles/firzok.json) describing what to keep. Nothing in
this module is specific to one person, so the same code can run per user in ApplyX with
a profile built from that user's settings.

evaluate(posting, profile) -> (verdict, reasons, meta)
  verdict: "match" | "review" | "excluded" | "ignore"
    match     fits every rule
    review    plausible but something is ambiguous (usually location scope)
    excluded  relevant role rejected by a rule worth logging (too senior, single country...)
    ignore    not relevant at all (wrong discipline, too old); not logged
  meta: priority (lower = better, from the matched location rule), location_class, years
"""
import json
import re

from .sources import norm_url, parse_date

DEFAULTS = {
    "max_age_hours": None,          # None = any age
    "source_lag_hours": {},          # extra hours for delayed feeds, e.g. {"remotive": 24}
    "title": {"include": [], "generic": [], "generic_requires": [], "exclude": [], "exclude_unless": []},
    "seniority": {"exclude": [], "ignore": []},
    "employment": {"exclude": []},
    "backend_only": {"pattern": None, "unless": None},
    "tech": {"exclude_title": [], "exclude_desc": [], "prefer_desc": [], "desc_min_hits": 3},
    "max_years": None,
    "location": {"remote_words": [], "accept": [], "reject": [], "countries": [],
                 "cities": [], "desc_hint": [], "unknown": "review"},
}


def _rx(words):
    """Compile a list of regex fragments into one case-insensitive alternation (or None)."""
    words = [w for w in (words or []) if w]
    return re.compile("|".join(f"(?:{w})" for w in words), re.I) if words else None


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


class Profile:
    def __init__(self, cfg):
        c = _merge(DEFAULTS, cfg)
        self.cfg = c
        self.name = c.get("name", "profile")
        self.max_age = c["max_age_hours"]
        self.lag = c["source_lag_hours"]
        t = c["title"]
        self.t_inc, self.t_gen, self.t_gen_req = _rx(t["include"]), _rx(t["generic"]), _rx(t["generic_requires"])
        self.t_exc, self.t_exc_unless = _rx(t["exclude"]), _rx(t["exclude_unless"])
        self.sen_exc, self.sen_ign = _rx(c["seniority"]["exclude"]), _rx(c["seniority"]["ignore"])
        self.emp_exc = _rx(c["employment"]["exclude"])
        self.be, self.be_unless = _rx([c["backend_only"]["pattern"]]), _rx([c["backend_only"]["unless"]])
        tc = c["tech"]
        self.tech_t, self.tech_d, self.tech_good = _rx(tc["exclude_title"]), _rx(tc["exclude_desc"]), _rx(tc["prefer_desc"])
        self.tech_min = tc["desc_min_hits"]
        self.max_years = c["max_years"]
        loc = c["location"]
        self.remote_rx = _rx(loc["remote_words"])
        self.accept = [dict(r, rx=_rx(r["match"])) for r in loc["accept"]]
        self.reject = [dict(r, rx=_rx(r["match"]), unless_rx=_rx(r.get("unless"))) for r in loc["reject"]]
        self.countries = _rx([rf"\b{x}\b" for x in loc["countries"]])
        self.cities = _rx([rf"\b{x}\b" for x in loc["cities"]])
        self.desc_hint = _rx(loc["desc_hint"])
        self.unknown = loc["unknown"]

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))


YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?", re.I)


NUM_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
NUM_WORDS_RE = re.compile(r"\b(" + "|".join(NUM_WORDS) + r")\b(?=\s*\+?\s*(?:\(\d+\)\s*)?years?)", re.I)


def required_years(desc):
    desc = NUM_WORDS_RE.sub(lambda m: str(NUM_WORDS[m.group(1).lower()]), desc[:8000])
    yrs = []
    for m in YEARS_RE.finditer(desc[:8000]):
        ctx = desc[max(0, m.start() - 40): m.end() + 40].lower()
        if "experience" in ctx or "professional" in ctx or "industry" in ctx:
            n = int(m.group(1))
            if 0 < n < 30:
                yrs.append(n)
    return min(yrs) if yrs else None


def classify_location(pr, loc, remote, desc):
    """Return (bucket, priority, reason). bucket: ok | review | exclude."""
    l = loc or ""
    is_remote = bool(remote) or bool(pr.remote_rx and pr.remote_rx.search(l))
    for r in pr.accept:
        if r["rx"] and r["rx"].search(l):
            if r.get("requires_remote") and not is_remote:
                continue
            prio = r.get("priority_if_remote", r["priority"]) if is_remote else r["priority"]
            return "ok", prio, r["name"] + (" (remote ok)" if is_remote and "priority_if_remote" in r else "")
    for r in pr.reject:
        if r["rx"] and r["rx"].search(l) and not (r["unless_rx"] and r["unless_rx"].search(l)):
            return "exclude", 9, r["name"]
    if pr.countries:
        found = sorted({m.group(0).lower() for m in pr.countries.finditer(l)})
        if found:
            return "exclude", 9, (f"single country ({found[0]})" if len(found) == 1 else f"country list ({', '.join(found)})")
    if pr.cities and pr.cities.search(l):
        if not is_remote:
            return "exclude", 9, f"onsite elsewhere ({l[:40]})"
        return "review", 5, f"remote tied to a city ({l[:60]})"
    if is_remote or not l:
        if pr.desc_hint and pr.desc_hint.search(desc[:4000]):
            return "review", 4, "remote; description hints at a wide scope"
        return ("review" if pr.unknown == "review" else "exclude"), 5, "remote scope unstated"
    return ("review" if pr.unknown == "review" else "exclude"), 5, f"unrecognized location '{l[:60]}'"


def evaluate(p, pr):
    t, d = p.get("title", ""), p.get("description", "")
    if pr.max_age is not None:
        age = p.get("age_hours")
        if age is None:
            return "ignore", ["no posting date"], {}
        if age > pr.max_age + pr.lag.get(p.get("source"), 0):
            return "ignore", ["too old"], {}
    hit_inc = pr.t_inc and pr.t_inc.search(t)
    hit_gen = pr.t_gen and pr.t_gen.search(t) and pr.t_gen_req and pr.t_gen_req.search(d[:3000])
    if not (hit_inc or hit_gen):
        return "ignore", ["title not a match"], {}
    if pr.t_exc and pr.t_exc.search(t) and not (pr.t_exc_unless and pr.t_exc_unless.search(t)):
        return "ignore", ["other discipline"], {}
    if pr.sen_exc and (m := pr.sen_exc.search(t)):
        return "excluded", [f"too senior ({m.group(0)})"], {}
    if pr.sen_ign and pr.sen_ign.search(t):
        return "ignore", ["too junior"], {}
    if pr.emp_exc and (pr.emp_exc.search(p.get("employment") or "") or pr.emp_exc.search(t)):
        return "excluded", ["contract/freelance/part-time"], {}
    if pr.be and pr.be.search(t) and not (pr.be_unless and pr.be_unless.search(t)):
        return "ignore", ["backend-only"], {}
    if pr.tech_t and (m := pr.tech_t.search(t)):
        return "excluded", [f"{m.group(0)} primary (title)"], {}
    bad = len(pr.tech_d.findall(d[:6000])) if pr.tech_d else 0
    good = len(pr.tech_good.findall(d[:6000])) if pr.tech_good else 0
    if bad >= pr.tech_min and bad > good:
        return "excluded", ["excluded tech is primary (description)"], {}
    yrs = required_years(d)
    if pr.max_years and yrs and yrs > pr.max_years:
        return "excluded", [f"{yrs}+ yrs required"], {"years": yrs}
    bucket, prio, why = classify_location(pr, p.get("location", ""), p.get("remote"), d)
    meta = {"priority": prio, "location_class": why, "years": yrs}
    if bucket == "exclude":
        return "excluded", [why], meta
    reasons = [f"excluded tech mentioned {bad}x in description"] if bad else []
    if bucket == "review":
        return "review", reasons + [why], meta
    return "match", reasons, meta


def apply(postings, pr, known_ids=(), known_urls=()):
    """Filter postings with a profile. Skips anything already known (by id or URL).
    Returns dict with match / review / excluded lists, each sorted by priority then age."""
    known_ids, known_urls = set(known_ids), set(known_urls)
    res = {"match": [], "review": [], "excluded": []}
    for p in postings:
        if p.get("id") in known_ids or (p.get("url") and norm_url(p["url"]) in known_urls):
            continue
        verdict, reasons, meta = evaluate(p, pr)
        if verdict == "ignore":
            continue
        q = dict(p, flags=reasons, **meta)
        q["description"] = (q.get("description") or "")[:1200]
        res[verdict].append(q)
    for k in res:
        res[k].sort(key=lambda p: (p.get("priority", 9), p.get("age_hours") if p.get("age_hours") is not None else 1e9))
    return res


__all__ = ["Profile", "evaluate", "apply", "classify_location", "required_years", "parse_date"]
