#!/usr/bin/env python3
"""Search IT job portals in Germany using the criteria in profile.toml.

Usage:
    python3 search.py                 # full search, writes results/<date>.md
    python3 search.py --days 7        # override how far back to look
    python3 search.py --remote-only   # only remote jobs
    python3 search.py --berlin-only   # only jobs in Berlin
    python3 search.py --min-score 40  # hide weak matches

Sources: Bundesagentur für Arbeit (API), Arbeitnow (API), LinkedIn (public
guest search), XING (public search page). Standard library only.
"""

import argparse
import base64
import html
import json
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


@dataclass
class Job:
    source: str
    title: str
    company: str
    location: str
    url: str
    published: date | None = None
    remote: bool = False
    description: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    also_on: list[str] = field(default_factory=list)
    excluded: str = ""
    new: bool = False

    @property
    def key(self) -> str:
        norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
        # Drop gender tags ("(m/w/d)", "w/m/d", "(all genders)") and "in <city>" suffixes.
        title = re.sub(r"\(?\b[mwfdx]\s*/\s*[mwfdx](\s*/\s*[mwfdx])*\b\)?|\((all|gn)[^)]*\)|\bin berlin\b",
                       "", self.title.lower())
        # Companies show up as "MILES" and "MILES Mobility GmbH": compare the first word.
        company = (re.findall(r"[a-z0-9]+", self.company.lower()) or [""])[0]
        return f"{norm(title)}|{company}"


def fetch(url: str, headers: dict | None = None, retries: int = 2) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
    return ""


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


# --- sources -----------------------------------------------------------------

def bundesagentur(cfg) -> list[Job]:
    base = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
    s = cfg["search"]
    searches = [{"wo": s["city"], "umkreis": s["radius_km"]}]
    if s.get("include_remote_germany"):
        searches.append({"arbeitszeit": "ho"})  # home office, anywhere in Germany
    jobs = []
    for query in s["queries"]:
        for extra in searches:
            params = {"was": query, "angebotsart": 1, "size": 100,
                      "veroeffentlichtseit": s["days"], **extra}
            data = json.loads(fetch(f"{base}?{urllib.parse.urlencode(params)}",
                                    {"X-API-Key": "jobboerse-jobsuche"}))
            for j in data.get("ergebnisliste", []):
                addr = (j.get("stellenlokationen") or [{}])[0].get("adresse", {})
                published = (j.get("veroeffentlichungszeitraum") or {}).get("von")
                jobs.append(Job(
                    source="Bundesagentur",
                    title=j.get("stellenangebotsTitel", ""),
                    company=j.get("firma", ""),
                    location=addr.get("ort", ""),
                    url=f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{j['referenznummer']}",
                    published=date.fromisoformat(published) if published else None,
                    remote=bool(j.get("homeofficemoeglich")),
                ))
    return jobs


def arbeitnow(cfg) -> list[Job]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["search"]["days"])
    jobs = []
    for page in range(1, 11):
        data = json.loads(fetch(f"https://www.arbeitnow.com/api/job-board-api?page={page}"))
        rows = data.get("data", [])
        for j in rows:
            created = datetime.fromtimestamp(j["created_at"], timezone.utc)
            if created < cutoff:
                continue
            jobs.append(Job(
                source="Arbeitnow",
                title=j["title"],
                company=j["company_name"],
                location=j.get("location", ""),
                url=j["url"],
                published=created.date(),
                remote=bool(j.get("remote")),
                description=strip_html(j.get("description", "")),
            ))
        if not rows or datetime.fromtimestamp(rows[-1]["created_at"], timezone.utc) < cutoff:
            break
    return jobs


def linkedin(cfg) -> list[Job]:
    s = cfg["search"]
    base = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    searches = [{"location": f"{s['city']}, Germany", "distance": round(s["radius_km"] / 1.6)}]
    if s.get("include_remote_germany"):
        searches.append({"location": "Germany", "f_WT": 2})  # remote only
    card_re = re.compile(r"<li>(.*?)</li>", re.S)
    jobs = []
    for query in s["queries"]:
        for extra in searches:
            for start in (0, 10, 20):
                params = {"keywords": query, "f_TPR": f"r{s['days'] * 86400}", "start": start, **extra}
                try:
                    page = fetch(f"{base}?{urllib.parse.urlencode(params)}")
                except Exception:
                    break  # LinkedIn throttles; keep what we have
                cards = card_re.findall(page)
                for c in cards:
                    get = lambda pat: (m := re.search(pat, c, re.S)) and html.unescape(m.group(1)).strip()
                    url = get(r'class="base-card__full-link[^"]*"\s+href="([^"?]+)')
                    if not url:
                        continue
                    published = get(r'<time[^>]*datetime="([^"]+)"')
                    jobs.append(Job(
                        source="LinkedIn",
                        title=get(r'base-search-card__title">(.*?)</h3>') or "",
                        company=strip_html(get(r'base-search-card__subtitle">(.*?)</h4>') or "").strip(),
                        location=get(r'job-search-card__location">(.*?)</span>') or "",
                        url=url,
                        published=date.fromisoformat(published) if published else None,
                        remote="f_WT" in extra,
                    ))
                if len(cards) < 10:
                    break
                time.sleep(1)
    return jobs


def xing(cfg) -> list[Job]:
    s = cfg["search"]
    cutoff = date.today() - timedelta(days=s["days"])
    decoder = json.JSONDecoder()
    jobs = []
    for query in s["queries"]:
        params = {"keywords": query, "location": s["city"], "radius": s["radius_km"]}
        page = fetch(f"https://www.xing.com/jobs/search?{urllib.parse.urlencode(params)}")
        # Jobs live in the page's embedded Apollo cache as "VisibleJob:<id>": {...}
        for m in re.finditer(r'"VisibleJob:[^"]+":', page):
            try:
                j, _ = decoder.raw_decode(page, m.end())
            except ValueError:
                continue
            refreshed = (j.get("refreshedAt") or "")[:10]
            published = date.fromisoformat(refreshed) if refreshed else None
            if published and published < cutoff:
                continue
            company = (j.get("companyInfo") or {}).get("companyNameOverride") or ""
            jobs.append(Job(
                source="XING",
                title=j.get("title", ""),
                company=company,
                location=((j.get("location") or {}).get("city")) or "",
                url=j.get("url", ""),
                published=published,
            ))
    return jobs


SOURCES = {"Bundesagentur": bundesagentur, "Arbeitnow": arbeitnow,
           "LinkedIn": linkedin, "XING": xing}


# --- descriptions ------------------------------------------------------------
# LinkedIn, XING and Bundesagentur search results carry no description, and the
# exclusion rules (German level, driving licence, hybrid) need one.

def describe_linkedin(job: Job) -> str:
    job_id = re.search(r"(\d+)/?$", job.url).group(1)
    page = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    m = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
    return strip_html(m.group(1)) if m else ""


def describe_xing(job: Job) -> str:
    page = fetch(job.url)
    m = re.search(r'"@type":"JobPosting".*?"description":("(?:[^"\\]|\\.)*")', page, re.S)
    return strip_html(json.loads(m.group(1))) if m else ""


def describe_bundesagentur(job: Job) -> str:
    refnr = base64.b64encode(job.url.rsplit("/", 1)[1].encode()).decode()
    data = json.loads(fetch(
        f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{refnr}",
        {"X-API-Key": "jobboerse-jobsuche"}))
    return data.get("stellenangebotsBeschreibung") or ""


DESCRIBERS = {"LinkedIn": describe_linkedin, "XING": describe_xing,
              "Bundesagentur": describe_bundesagentur}


def add_descriptions(jobs: list[Job]) -> None:
    def work(job: Job) -> None:
        try:
            job.description = DESCRIBERS[job.source](job)
        except Exception:
            pass  # the job stays, flagged as "descrição indisponível"

    todo = [j for j in jobs if not j.description and j.source in DESCRIBERS]
    print(f"lendo {len(todo)} descrições...", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, todo))
    for job in jobs:
        job.description = re.sub(r"\s+", " ", job.description).strip()


# --- filtering and scoring ---------------------------------------------------

def compile_all(patterns):
    return [re.compile(p, re.I) for p in patterns]


def in_city(job: Job, cfg) -> bool:
    return cfg["search"]["city"].lower() in job.location.lower()


def prefilter(jobs: list[Job], cfg) -> list[Job]:
    """Cheap checks on title and location, before fetching descriptions."""
    f = cfg["filter"]
    title_strong, title_weak = compile_all(f["title_strong"]), compile_all(f["title_weak"])
    software = compile_all(f["software_signal"])
    title_none = compile_all(f["title_none"])
    kept = []
    for job in jobs:
        if any(p.search(job.title) for p in title_none):
            continue
        if not any(p.search(job.title) for p in title_strong):
            weak = any(p.search(job.title) for p in title_weak)
            text = f"{job.title} {job.description}"
            if not (weak and any(p.search(text) for p in software)):
                continue
        # Arbeitnow also lists jobs from its Swiss/Dutch/Austrian sites.
        if re.match(r"https://www\.arbeitnow\.(?!com/)", job.url):
            continue
        if not in_city(job, cfg) and not job.remote and "remote" not in job.location.lower():
            continue
        kept.append(job)
    return kept


def exclusion(job: Job, cfg) -> str:
    """Why the job does not fit, from its description; empty when it fits."""
    e = {k: compile_all(v) for k, v in cfg["exclude"].items()}
    if not job.description:
        hit = next((m.group(0) for p in e["other_fields"] if (m := p.search(job.title))), None)
        return f"setor fora do seu perfil (“{hit}”)" if hit else ""
    has = lambda name: next((m.group(0) for p in e[name] if (m := p.search(job.description))), None)
    if hit := has("german_required"):
        return f"pede alemão acima de B1 (“{hit}”)"
    if (m := next((m for p in e["other_fields"] if (m := p.search(f"{job.title} {job.description}"))), None)):
        return f"setor fora do seu perfil (“{m.group(0)}”)"
    if hit := has("driving_license"):
        return f"pede carteira de motorista (“{hit}”)"
    if not in_city(job, cfg):
        if (hit := has("onsite")) and not has("full_remote"):
            return f"fora de {cfg['search']['city']} e híbrida/presencial (“{hit}”)"
        if not has("remote_words") and not has("full_remote"):
            return f"fora de {cfg['search']['city']} e não diz que é remota"
    return ""


def score(job: Job, cfg) -> None:
    # 0-100: base 50 (title and location already fit), seniority 15, skills 25,
    # freshness 7, English description 3.
    seniority = compile_all(cfg["score"]["seniority"])
    skills = {p: re.compile(p, re.I) for p in cfg["score"]["skills"]}
    text = f"{job.title} {job.description}"
    points, reasons = 50, []
    if any(p.search(job.title) for p in seniority):
        points += 15
        reasons.append("sênior/lead")
    found = [re.sub(r"\\b|\.?\?", "", p) for p, r in skills.items() if r.search(text)]
    if found:
        points += min(len(found) * 5, 25)
        reasons.append(", ".join(found))
    if job.description and re.search(r"\b(the|and|you|with)\b", job.description[:500], re.I):
        points += 3
        reasons.append("descrição em inglês")
    elif not job.description:
        reasons.append("⚠️ descrição indisponível: confira idioma e modelo de trabalho")
    if job.published:
        points += max(0, 7 - (date.today() - job.published).days)
    job.score, job.reasons = points, reasons


SOURCE_RANK = {"Arbeitnow": 0, "LinkedIn": 1, "XING": 2, "Bundesagentur": 3}


def dedupe(jobs: list[Job]) -> list[Job]:
    """One copy per job: the one with a description, else from the most reliable source."""
    groups: dict[str, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.key, []).append(job)
    kept = []
    for copies in groups.values():
        copies.sort(key=lambda j: (-len(j.description), SOURCE_RANK.get(j.source, 9)))
        best = copies[0]
        best.remote = any(j.remote for j in copies)
        best.also_on = sorted({j.source for j in copies} - {best.source})
        kept.append(best)
    return kept


# --- output ------------------------------------------------------------------

def load_applied() -> tuple[set[str], set[str]]:
    """Keys and URLs of jobs Graciela already applied to (data/applied.json)."""
    path = ROOT / "data" / "applied.json"
    applied = json.loads(path.read_text()) if path.exists() else []
    keys = {Job("", a["title"], a["company"], "", "").key for a in applied}
    return keys, {a["url"] for a in applied if a.get("url")}


def mark_new(jobs: list[Job]) -> None:
    seen_file = ROOT / "data" / "seen.json"
    seen = json.loads(seen_file.read_text()) if seen_file.exists() else {}
    today = date.today().isoformat()
    for job in jobs:
        if job.key not in seen:
            job.new = True
            seen[job.key] = today
    seen_file.parent.mkdir(exist_ok=True)
    seen_file.write_text(json.dumps(seen, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def to_markdown(jobs: list[Job], excluded: list[Job], applied: int, counts: dict, errors: dict, cfg) -> str:
    city = cfg["search"]["city"]
    md = [f"# Vagas — {date.today():%d/%m/%Y}", "",
          f"**{len(jobs)} vagas** ({sum(j.new for j in jobs)} 🆕 novas desde a última busca) · "
          f"{len(excluded)} descartadas pela descrição (lista no fim) · "
          f"{applied} já aplicadas (ocultas)", "",
          "Fontes: " + ", ".join(f"{s} ({n})" for s, n in counts.items())]
    for s, e in errors.items():
        md.append(f"\n> ⚠️ {s} falhou: {e}")

    def card(n: int, j: Job) -> list[str]:
        where = j.location + (" · remoto" if j.remote and city.lower() not in j.location.lower() else "")
        when = f" · {j.published:%d/%m}" if j.published else ""
        source = j.source + (f" (também {', '.join(j.also_on)})" if j.also_on else "")
        lines = [f"{n}. {'🆕 ' if j.new else ''}**[{j.title.strip()}]({j.url})** — {j.score}",
                 f"   <br>{j.company} · {where}{when} · {source}"]
        if j.reasons:
            lines.append(f"   <br>_{' · '.join(j.reasons)}_")
        return lines

    n = 0
    for heading, low, high in (("⭐ Combinam muito (80+)", 80, 999),
                               ("👍 Boas (65–79)", 65, 79), ("Outras (< 65)", 0, 64)):
        group = [j for j in jobs if low <= j.score <= high]
        if not group:
            continue
        md += ["", f"## {heading}", ""]
        for j in group:
            n += 1
            md += card(n, j)

    if excluded:
        md += ["", f"## Descartadas ({len(excluded)})", "",
               "Ficaram de fora por algo na descrição. Se alguma não devia sair, me avise "
               "para ajustar o `profile.toml`.", ""]
        for j in sorted(excluded, key=lambda j: j.excluded):
            md.append(f"- [{j.title.strip()}]({j.url}) — {j.company}, {j.location}: {j.excluded}")
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    ap.add_argument("--remote-only", action="store_true")
    ap.add_argument("--berlin-only", action="store_true")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--sources", help="comma-separated, e.g. LinkedIn,XING")
    ap.add_argument("--extra", action="append", metavar="FILE",
                    help="JSON list of extra jobs (see browser/README.md); repeatable")
    args = ap.parse_args()

    cfg = tomllib.loads((ROOT / "profile.toml").read_text())
    if args.days:
        cfg["search"]["days"] = args.days
    if args.berlin_only:
        cfg["search"]["include_remote_germany"] = False

    wanted = args.sources.split(",") if args.sources else list(SOURCES)
    jobs, counts, errors = [], {}, {}
    for name in wanted:
        print(f"buscando {name}...", file=sys.stderr)
        try:
            found = SOURCES[name](cfg)
            counts[name] = len(found)
            jobs += found
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
            print(f"  falhou: {errors[name]}", file=sys.stderr)

    # Jobs collected by hand or in a real browser (Indeed, Glassdoor, GermanTechJobs).
    for path in args.extra or []:
        for j in json.loads(Path(path).read_text()):
            published = j.get("published")
            job = Job(**{**j, "published": date.fromisoformat(published) if published else None})
            counts[job.source] = counts.get(job.source, 0) + 1
            jobs.append(job)

    for job in jobs:
        job.title = re.sub(r"\s+", " ", job.title).strip()
    jobs = dedupe(prefilter(jobs, cfg))
    add_descriptions(jobs)
    applied_keys, applied_urls = load_applied()
    before = len(jobs)
    jobs = [j for j in jobs if j.key not in applied_keys and j.url not in applied_urls]
    applied = before - len(jobs)
    excluded = []
    for job in jobs:
        job.excluded = exclusion(job, cfg)
        if job.excluded:
            excluded.append(job)
        else:
            score(job, cfg)
    jobs = [j for j in jobs if not j.excluded]
    if args.remote_only:
        jobs = [j for j in jobs if j.remote or "remote" in j.location.lower()]
    if args.berlin_only:
        jobs = [j for j in jobs if cfg["search"]["city"].lower() in j.location.lower()]
    jobs = [j for j in jobs if j.score >= args.min_score]
    jobs.sort(key=lambda j: (j.score, j.published or date.min), reverse=True)
    mark_new(jobs)

    out = ROOT / "results" / f"{date.today().isoformat()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(to_markdown(jobs, excluded, applied, counts, errors, cfg))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
