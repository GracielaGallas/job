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
import html
import json
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
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


# --- scoring -----------------------------------------------------------------

def compile_all(patterns):
    return [re.compile(p, re.I) for p in patterns]


def score_jobs(jobs: list[Job], cfg) -> list[Job]:
    f = cfg["filter"]
    title_strong, title_weak = compile_all(f["title_strong"]), compile_all(f["title_weak"])
    software = compile_all(f["software_signal"])
    title_none = compile_all(f["title_none"])
    seniority = compile_all(cfg["score"]["seniority"])
    skills = {p: re.compile(p, re.I) for p in cfg["score"]["skills"]}
    german = compile_all(cfg["score"]["german_required"])
    city = cfg["search"]["city"].lower()
    today = date.today()

    kept = []
    for job in jobs:
        text = f"{job.title} {job.description}"
        if any(p.search(job.title) for p in title_none):
            continue
        if not any(p.search(job.title) for p in title_strong):
            weak = any(p.search(job.title) for p in title_weak)
            if not (weak and any(p.search(text) for p in software)):
                continue
        # Arbeitnow also lists jobs from its Swiss/Dutch/Austrian sites.
        if re.match(r"https://www\.arbeitnow\.(?!com/)", job.url):
            continue
        in_city = city in job.location.lower()
        remote = job.remote or "remote" in job.location.lower()
        if not in_city and not remote:
            continue

        # Score 0-100: base 40, seniority 15, skills 25, location 10, freshness 7, English 3.
        score, reasons = 40, []
        if any(p.search(job.title) for p in seniority):
            score += 15
            reasons.append("nível sênior/lead")
        found = [re.sub(r"\\b|\.?\?", "", p) for p, r in skills.items() if r.search(text)]
        if found:
            score += min(len(found) * 5, 25)
            reasons.append("skills: " + ", ".join(found))
        score += 10
        reasons.append(" + ".join(x for x, ok in ((cfg["search"]["city"], in_city), ("remoto", remote)) if ok))
        if job.description and any(p.search(job.description) for p in german):
            score -= 25
            reasons.append("⚠️ pede alemão fluente")
        elif job.description and re.search(r"\b(the|and|you|with)\b", job.description[:500], re.I):
            score += 3
            reasons.append("descrição em inglês")
        if job.published:
            score += max(0, 7 - (today - job.published).days)
        job.score, job.reasons = score, reasons
        kept.append(job)
    return kept


def dedupe(jobs: list[Job]) -> list[Job]:
    best: dict[str, Job] = {}
    for job in jobs:
        current = best.get(job.key)
        # Prefer the copy with more information (description), then the higher score.
        if current is None or (len(job.description), job.score) > (len(current.description), current.score):
            if current and current.source != job.source:
                job.reasons.append(f"também no {current.source}")
            best[job.key] = job
        elif job.source != current.source and f"também no {job.source}" not in current.reasons:
            current.reasons.append(f"também no {job.source}")
    return list(best.values())


# --- output ------------------------------------------------------------------

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


def to_markdown(jobs: list[Job], counts: dict, errors: dict) -> str:
    md = [f"# Vagas — {date.today().isoformat()}", ""]
    md.append(f"{len(jobs)} vagas, {sum(j.new for j in jobs)} novas desde a última busca. "
              "Fontes: " + ", ".join(f"{s} ({n})" for s, n in counts.items()))
    for s, e in errors.items():
        md.append(f"\n> ⚠️ {s} falhou: {e}")
    md += ["", "| # | Score | Vaga | Empresa | Local | Publicada | Fonte | Por quê |",
           "|---|---|---|---|---|---|---|---|"]
    esc = lambda s: (s or "").replace("|", "/").strip()
    for i, j in enumerate(jobs, 1):
        title = f"{'🆕 ' if j.new else ''}[{esc(j.title)}]({j.url})"
        md.append(f"| {i} | {j.score} | {title} | {esc(j.company)} | {esc(j.location)}"
                  f"{' (remoto)' if j.remote else ''} | {j.published or ''} | {j.source} | "
                  f"{esc('; '.join(j.reasons))} |")
    return "\n".join(md) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    ap.add_argument("--remote-only", action="store_true")
    ap.add_argument("--berlin-only", action="store_true")
    ap.add_argument("--min-score", type=int, default=0)
    ap.add_argument("--sources", help="comma-separated, e.g. LinkedIn,XING")
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

    jobs = dedupe(score_jobs(jobs, cfg))
    if args.remote_only:
        jobs = [j for j in jobs if j.remote or "remote" in j.location.lower()]
    if args.berlin_only:
        jobs = [j for j in jobs if cfg["search"]["city"].lower() in j.location.lower()]
    jobs = [j for j in jobs if j.score >= args.min_score]
    jobs.sort(key=lambda j: (j.score, j.published or date.min), reverse=True)
    mark_new(jobs)

    out = ROOT / "results" / f"{date.today().isoformat()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(to_markdown(jobs, counts, errors))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
