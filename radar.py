#!/usr/bin/env python3
"""AEC Radar: finds new public repos matching CAD/BIM keywords and writes an Atom feed.

Standard library only. Reads config.json, keeps state in state/, writes public/feed.xml.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

STATE_DIR = os.path.join(ROOT, "state")
SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
ITEMS_PATH = os.path.join(STATE_DIR, "items.json")
OUT_DIR = os.path.join(ROOT, "public")

TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
NOW = dt.datetime.now(dt.timezone.utc)
SC = CFG["scoring"]

# Search API: 30 req/min with a token, 10 req/min without.
SEARCH_DELAY = 2.2 if TOKEN else 6.5


# ---------------------------------------------------------------- helpers

def log(msg):
    print(msg, file=sys.stderr, flush=True)


def http_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "aec-radar", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        log(f"  ! HTTP {e.code} for {url}")
    except Exception as e:  # network errors should not kill the whole run
        log(f"  ! {type(e).__name__}: {e} for {url}")
    return None


def gh_headers():
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def parse_time(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def has_term(text, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- sources

def github_search(query, since, extra="", sort="updated"):
    q = f"{query} created:>={since} fork:false archived:false {extra}".strip()
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": q, "sort": sort, "order": "desc", "per_page": 50})
    data = http_json(url, gh_headers())
    time.sleep(SEARCH_DELAY)
    out = []
    for r in (data or {}).get("items", []):
        homepage = (r.get("homepage") or "").strip()
        if "github.com/" in homepage and r["full_name"].lower() in homepage.lower():
            homepage = ""  # homepage that just points back at the repo
        out.append({
            "id": "gh:" + r["full_name"].lower(),
            "source": "GitHub",
            "name": r["full_name"],
            "owner": r["owner"]["login"],
            "url": r["html_url"],
            "description": (r.get("description") or "").strip(),
            "homepage": homepage,
            "stars": r.get("stargazers_count", 0),
            "language": r.get("language") or "",
            "topics": r.get("topics") or [],
            "created_at": r.get("created_at"),
            "pushed_at": r.get("pushed_at"),
            "query": query,
        })
    return out


def github_root_files(full_name):
    data = http_json(f"https://api.github.com/repos/{full_name}/contents", gh_headers())
    if isinstance(data, list):
        return [x.get("name", "") for x in data]
    return []


def gitlab_search(term, since):
    url = "https://gitlab.com/api/v4/projects?" + urllib.parse.urlencode({
        "search": term, "order_by": "created_at", "sort": "desc", "visibility": "public",
        "per_page": 50, "created_after": f"{since}T00:00:00Z"})
    data = http_json(url)
    time.sleep(1)
    out = []
    for p in data or []:
        created = parse_time(p.get("created_at"))
        if created and created.date() < since:
            continue
        if p.get("forked_from_project") or p.get("archived"):
            continue
        out.append({
            "id": "gl:" + p["path_with_namespace"].lower(),
            "source": "GitLab",
            "name": p["path_with_namespace"],
            "owner": p.get("namespace", {}).get("path", ""),
            "url": p["web_url"],
            "description": (p.get("description") or "").strip(),
            "homepage": "",
            "stars": p.get("star_count", 0),
            "language": "",
            "topics": p.get("topics") or p.get("tag_list") or [],
            "created_at": p.get("created_at"),
            "pushed_at": p.get("last_activity_at"),
            "query": term,
        })
    return out


def codeberg_search(term, since):
    url = "https://codeberg.org/api/v1/repos/search?" + urllib.parse.urlencode({
        "q": term, "includeDesc": "true", "sort": "created", "order": "desc", "limit": 50})
    data = http_json(url)
    time.sleep(1)
    out = []
    for r in (data or {}).get("data", []):
        created = parse_time(r.get("created_at"))
        if created and created.date() < since:
            continue
        if r.get("fork") or r.get("archived") or r.get("private"):
            continue
        out.append({
            "id": "cb:" + r["full_name"].lower(),
            "source": "Codeberg",
            "name": r["full_name"],
            "owner": (r.get("owner") or {}).get("login", ""),
            "url": r["html_url"],
            "description": (r.get("description") or "").strip(),
            "homepage": (r.get("website") or "").strip(),
            "stars": r.get("stars_count", 0),
            "language": r.get("language") or "",
            "topics": r.get("topics") or [],
            "created_at": r.get("created_at"),
            "pushed_at": r.get("updated_at"),
            "query": term,
        })
    return out


# ---------------------------------------------------------------- scoring

def score(item, root_files=None):
    name_text = item["name"].split("/")[-1].lower().replace("_", " ").replace("-", " ")
    text = " ".join([item["name"].replace("_", " "), item["description"], " ".join(item["topics"])]).lower()
    s, flags = 0, []

    if item["homepage"]:
        s += 3
        flags.append("DEMO")

    brand = [t for t in SC["brand_terms"] if has_term(text, t)]
    fmt = [t for t in SC["format_terms"] if has_term(text, t)]
    if brand or fmt:
        s += 2
        flags.append("AEC")
    elif any(has_term(text, t) for t in SC["domain_terms"]):
        s += 1

    topics = {t.lower() for t in item["topics"]}
    if topics & set(SC["topic_terms"]):
        s += 2

    # "SketchUp-style", "push/pull", "clean-room", "alternative"… = the clone genre.
    if any(has_term(text, t) for t in SC.get("clone_terms", [])):
        s += 2
        flags.append("CLONE")

    # Runs in the browser = easy to demo on camera.
    if any(has_term(text, t) for t in SC.get("web_terms", [])):
        s += 1
        flags.append("WEB")

    ai_files = set(SC["ai_files"]) & set(root_files or [])
    if ai_files or any(has_term(text, t) for t in SC["ai_terms"]):
        s += 1
        flags.append("AI")

    pushed = parse_time(item.get("pushed_at"))
    if pushed and NOW - pushed < dt.timedelta(hours=48):
        s += 1
    if item["stars"] >= 5:
        s += 1
    if item.get("rising"):
        s += 2
        flags.append("RISING")

    if not item["description"]:
        s -= 2
    if any(has_term(name_text, t) for t in SC["negative_terms"]):
        s -= 3

    # Pirated-software / malware lures ("pre-activated", "crack", "full version"…): never publish.
    if any(has_term(text, t) or has_term(name_text, t) for t in SC.get("spam_terms", [])):
        s = -99

    # A repo must show some CAD/BIM relevance; a demo link alone isn't enough.
    item["relevant"] = bool(brand or fmt or (topics & set(SC["topic_terms"]))
                            or any(has_term(text, t) for t in SC["domain_terms"]))

    item["score"] = s
    item["flags"] = flags
    item["matched"] = brand + fmt
    item["ai_files"] = sorted(ai_files)
    return s


# ---------------------------------------------------------------- feed

def entry_html(it):
    rows = [f"<p>{html.escape(it['description'] or '(no description)')}</p><ul>"]
    if it["homepage"]:
        h = html.escape(it["homepage"])
        rows.append(f'<li><b>Live demo:</b> <a href="{h}">{h}</a></li>')
    rows.append(f"<li><b>Source:</b> {it['source']} · ⭐ {it['stars']}"
                + (f" · {html.escape(it['language'])}" if it["language"] else "") + "</li>")
    created = (it.get("created_at") or "")[:10]
    rows.append(f"<li><b>Created:</b> {created}</li>")
    if it["topics"]:
        rows.append(f"<li><b>Topics:</b> {html.escape(', '.join(it['topics']))}</li>")
    if it["matched"]:
        rows.append(f"<li><b>Matched:</b> {html.escape(', '.join(it['matched']))}</li>")
    if it["ai_files"]:
        rows.append(f"<li><b>AI files in repo:</b> {html.escape(', '.join(it['ai_files']))}</li>")
    rows.append(f"<li><b>Score:</b> {it['score']} · query: <code>{html.escape(it['query'])}</code></li></ul>")
    return "".join(rows)


def entry_title(it):
    tags = "".join(f"[{f}]" for f in it["flags"])
    desc = it["description"][:90] + ("…" if len(it["description"]) > 90 else "")
    return f"★{it['score']} {tags} {it['name']}" + (f": {desc}" if desc else "")


def write_feed(items):
    os.makedirs(OUT_DIR, exist_ok=True)
    site = CFG["feed"]["site_url"]
    e = html.escape
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<feed xmlns="http://www.w3.org/2005/Atom">',
             f"<title>{e(CFG['feed']['title'])}</title>",
             f'<link href="{e(site)}feed.xml" rel="self"/>',
             f'<link href="{e(site)}"/>',
             f"<id>{e(site)}</id>",
             f"<updated>{iso(NOW)}</updated>"]
    for it in items:
        parts += ["<entry>",
                  f"<title>{e(entry_title(it))}</title>",
                  f'<link href="{e(it["url"])}"/>',
                  f"<id>{e(it['url'])}</id>",
                  f"<updated>{it['found_at']}</updated>",
                  f"<published>{it['found_at']}</published>",
                  f"<author><name>{e(it['owner'] or it['source'])}</name></author>",
                  f'<content type="html">{e(entry_html(it))}</content>',
                  "</entry>"]
    parts.append("</feed>")
    with open(os.path.join(OUT_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    rows = "".join(
        f'<li><a href="{e(it["url"])}">{e(entry_title(it))}</a> <small>{it["found_at"][:10]}</small></li>'
        for it in items)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AEC Radar</title>
<link rel="alternate" type="application/atom+xml" title="AEC Radar" href="feed.xml">
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 16px}}li{{margin:.4rem 0}}</style>
</head><body><h1>AEC Radar</h1><p>Feed: <a href="feed.xml">feed.xml</a> · updated {iso(NOW)}</p>
<ul>{rows}</ul></body></html>"""
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------------------------- main

def main():
    since = (NOW - dt.timedelta(days=CFG["window_days"])).date()
    seen = load_json(SEEN_PATH, {})
    items = load_json(ITEMS_PATH, [])
    cands = {}

    def add(lst):
        for c in lst:
            cands.setdefault(c["id"], c)

    gh = CFG["github"]
    for q in gh["queries"]:
        log(f"GitHub: {q}")
        add(github_search(q, since))

    rising = gh.get("rising")
    if rising:
        r_since = (NOW - dt.timedelta(days=rising["window_days"])).date()
        for q in rising["queries"]:
            log(f"GitHub rising: {q}")
            for c in github_search(q, r_since, extra=f"stars:>={rising['min_stars']}", sort="stars"):
                c["rising"] = True
                cands[c["id"]] = {**cands.get(c["id"], {}), **c}

    if CFG.get("gitlab", {}).get("enabled"):
        for t in CFG["gitlab"]["terms"]:
            log(f"GitLab: {t}")
            add(gitlab_search(t, since))

    if CFG.get("codeberg", {}).get("enabled"):
        for t in CFG["codeberg"]["terms"]:
            log(f"Codeberg: {t}")
            add(codeberg_search(t, since))

    new = [c for c in cands.values() if c["id"] not in seen]
    for c in new:
        score(c)

    # Check root files (CLAUDE.md, AGENTS.md…) only for the most promising GitHub repos.
    gh_new = sorted((c for c in new if c["source"] == "GitHub"), key=lambda c: -c["score"])
    for c in gh_new[: gh.get("max_root_checks", 40)]:
        if c["score"] >= CFG["min_score"] - 1:
            score(c, github_root_files(c["name"]))

    published = sorted((c for c in new if c["score"] >= CFG["min_score"] and c["relevant"]),
                       key=lambda c: -c["score"])
    stamp = iso(NOW)
    for c in published:
        c["found_at"] = stamp
        seen[c["id"]] = stamp

    # Low scorers are not marked seen, so they get re-scored while still inside the window.
    cutoff = NOW - dt.timedelta(days=180)
    seen = {k: v for k, v in seen.items() if (parse_time(v) or NOW) > cutoff}

    items = (published + items)[: CFG["feed"]["max_items"]]
    save_json(SEEN_PATH, seen)
    save_json(ITEMS_PATH, items)
    write_feed(items)

    log(f"\nCandidates: {len(cands)} · new: {len(new)} · published: {len(published)} · feed items: {len(items)}")
    for c in published:
        log(f"  {entry_title(c)}")


if __name__ == "__main__":
    main()
