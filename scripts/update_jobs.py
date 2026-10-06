
#!/usr/bin/env python3
"""Daily job refresh for Remote Job Radar.

Pulls public job feeds (Remotive, RemoteOK, Himalayas, We Work Remotely, Hacker News
"Who is hiring"), keeps senior frontend / full-stack roles, scores them for agentic AI
work, adds new ones to data/jobs.json and writes new_jobs.md for the daily alert issue.
Standard library only. Every source is wrapped so one failure never stops the run.
"""
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "jobs.json"
ALERT = ROOT / "new_jobs.md"
TODAY = date.today()
IST = timezone(timedelta(hours=5, minutes=30))
EXPIRE_DAYS = 21
UA = {"User-Agent": "Mozilla/5.0 (remote-job-radar; +https://github.com/Rutuja-Dond/remote-job)"}

FRONT = re.compile(r"\b(react|next\.?js|frontend|front-end|front end|ui engineer|full[- ]?stack|javascript|typescript)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|lead|principal|founding|architect)\b|\b([5-9]|1\d)\+?\s*(years|yrs)", re.I)
JUNIOR = re.compile(r"\b(junior|intern|internship|entry[- ]level|graduate)\b", re.I)
AGENT = re.compile(r"\b(agent|agents|agentic|mcp|multi-agent|langgraph|langchain|rag)\b", re.I)
AI = re.compile(r"\b(llm|openai|anthropic|claude|gemini|generative|gen ?ai|ai[- ]native|ai[- ]powered|machine learning|artificial intelligence)\b|\bAI\b", re.I)
REMOTE = re.compile(r"remote|anywhere|worldwide|work from home|distributed", re.I)

REGION_RULES = [
    ("India", r"\bindia\b"), ("Canada", r"\bcanada\b"), ("Japan", r"\bjapan|tokyo\b"),
    ("Saudi", r"saudi|riyadh|\bksa\b"), ("Australia", r"australia|sydney|melbourne|\banz\b"),
    ("Africa", r"africa|nigeria|kenya|egypt|ghana|south africa|\bemea\b"),
    ("USA", r"\busa?\b|united states|\bnorth america\b|\bamericas\b"),
]
OPEN_WORLD = re.compile(r"worldwide|anywhere|global|any location|all countries|\bindia\b", re.I)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url):
    return json.loads(get(url))


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def regions_for(loc):
    out = [name for name, pat in REGION_RULES if re.search(pat, loc, re.I)]
    if not out or OPEN_WORLD.search(loc):
        out.append("Global")
    return sorted(set(out))


def make(source, title, company, loc, desc, url, posted=""):
    title, company, loc = clean(title), clean(company), clean(loc) or "Remote"
    blob = f"{title} {desc}"
    if not title or not company or not url:
        return None
    if JUNIOR.search(title) or not (FRONT.search(title) or (FRONT.search(blob) and re.search(r"react|next", blob, re.I))):
        return None
    if not SENIOR.search(title) and not SENIOR.search(desc):
        return None
    if not REMOTE.search(f"{loc} {desc[:600]} {title}") and "remote" not in source:
        return None
    ai = bool(AI.search(blob)) or bool(AGENT.search(blob))
    agent = bool(AGENT.search(blob))
    title_front = bool(FRONT.search(title))
    if ai and agent and title_front:
        pri = 1
    elif ai:
        pri = 2
    else:
        pri = 3
    m = re.search(r"\b([5-9]|1\d)\+?\s*(?:years|yrs)", desc, re.I)
    years = f"{m.group(1)}+ years" if m else "Senior, years not stated"
    why = {1: "Frontend role with agentic AI work (agents, LLMs, MCP or RAG) in the description.",
           2: "React or full-stack role with AI features in the product.",
           3: "Senior frontend or full-stack role. No AI focus found in the listing."}[pri]
    reg = regions_for(loc)
    note = "" if OPEN_WORLD.search(loc) else "Check eligibility from India"
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:40]
    return {"id": slug, "pri": pri, "added": TODAY.isoformat(), "reg": reg, "title": title[:120],
            "co": company[:60], "loc": loc[:90], "years": years, "overlap": "", "why": why,
            "url": url, **({"note": note} if note else {}), "_src": source}


def src_remotive():
    out = []
    for q in ("react", "next.js", "frontend ai", "full stack ai"):
        d = get_json("https://remotive.com/api/remote-jobs?limit=100&search=" + urllib.parse.quote(q))
        for j in d.get("jobs", []):
            out.append(make("remotive", j.get("title"), j.get("company_name"),
                            j.get("candidate_required_location"), clean(j.get("description")), j.get("url")))
    return out


def src_remoteok():
    d = get_json("https://remoteok.com/api")
    out = []
    for j in d:
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(make("remoteok", j.get("position"), j.get("company"), j.get("location") or "Worldwide",
                        clean(j.get("description")) + " " + " ".join(j.get("tags") or []), j.get("url")))
    return out


def src_himalayas():
    out = []
    for q in ("react", "nextjs", "frontend"):
        d = get_json("https://himalayas.app/jobs/api/search?q=" + urllib.parse.quote(q))
        for j in d.get("jobs", []):
            loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
            out.append(make("himalayas", j.get("title"), j.get("companyName"), loc,
                            clean(j.get("description")) + " " + str(j.get("seniority") or ""),
                            j.get("applicationLink") or j.get("guid")))
    return out


def src_wwr():
    out = []
    for cat in ("remote-front-end-programming-jobs", "remote-full-stack-programming-jobs"):
        root = ET.fromstring(get(f"https://weworkremotely.com/categories/{cat}.rss"))
        for it in root.iter("item"):
            full = it.findtext("title") or ""
            company, _, title = full.partition(":")
            if not title:
                company, title = "", full
            out.append(make("weworkremotely", title, company, it.findtext("region") or "Worldwide",
                            clean(it.findtext("description")), it.findtext("link")))
    return out


def src_hn():
    d = get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6")
    story = next((h for h in d["hits"] if (h.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
    if not story:
        return []
    items = get_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
    out = []
    for c in items.get("children", []):
        text = clean((c.get("text") or "").replace("<p>", " \n "))
        if not text or not re.search(r"\bremote\b", text, re.I):
            continue
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            continue
        out.append(make("hn-remote", parts[1] if len(parts) > 1 else "Engineer", parts[0],
                        " ".join(parts[2:4]), text, f"https://news.ycombinator.com/item?id={c['id']}"))
    return out


SOURCES = [("Remotive", src_remotive), ("RemoteOK", src_remoteok), ("Himalayas", src_himalayas),
           ("We Work Remotely", src_wwr), ("Hacker News Who is Hiring", src_hn)]


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    jobs, meta = data["jobs"], data["meta"]
    cutoff = (TODAY - timedelta(days=EXPIRE_DAYS)).isoformat()
    jobs = [j for j in jobs if j.get("added", "9999") >= cutoff]
    known = {re.sub(r"[^a-z0-9]", "", j["co"].lower()) for j in jobs}

    scanned, failed, found = [], [], []
    for name, fn in SOURCES:
        try:
            rows = [r for r in fn() if r]
            scanned.append(name)
            found.extend(rows)
        except Exception as e:  # keep going, report in META.boards
            failed.append(name)
            print(f"[warn] {name} failed: {e}", file=sys.stderr)

    if not scanned:
        print("All sources failed; leaving data unchanged.", file=sys.stderr)
        sys.exit(1)

    found.sort(key=lambda j: j["pri"])
    new = []
    for j in found:
        key = re.sub(r"[^a-z0-9]", "", j["co"].lower())
        if key in known:
            continue
        known.add(key)
        j.pop("_src", None)
        used = {x["id"] for x in jobs + new}
        n, base = 2, j["id"]
        while j["id"] in used:
            j["id"], n = f"{base}-{n}", n + 1
        new.append(j)

    jobs.extend(new)
    now = datetime.now(IST)
    meta["date"] = TODAY.isoformat()
    meta["dateline"] = "Daily update · " + now.strftime("%A, %-d %B %Y")
    meta["signal"] = (f"{len(new)} new role(s) found in today's automated scan of public job feeds. "
                      "Hiring announcements are not tracked automatically; check company careers pages.")
    meta["boards"] = ("Scanned automatically: " + ", ".join(scanned or ["none"]) + ". "
                      + ("Could not reach today: " + ", ".join(failed) + ". " if failed else "")
                      + "LinkedIn, Naukri, Indeed and Glassdoor need a login and are not scanned. "
                        "Confirm date and eligibility on each apply link.")
    DATA.write_text(json.dumps({"meta": meta, "jobs": jobs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if new:
        lines = [f"# {len(new)} new remote roles · {TODAY:%d %b %Y}", "",
                 "Live board: https://rutuja-dond.github.io/remote-job/#jobs", ""]
        for j in sorted(new, key=lambda x: x["pri"]):
            lines.append(f"- **P{j['pri']}** [{j['title']} at {j['co']}]({j['url']}) · {j['loc']} · {', '.join(j['reg'])}")
        ALERT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif ALERT.exists():
        ALERT.unlink()
    print(f"new={len(new)} total={len(jobs)} scanned={scanned} failed={failed}")


if __name__ == "__main__":
    main()
