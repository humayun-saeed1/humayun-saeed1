#!/usr/bin/env python3
"""
Renders an animated "GitHub Pulse" dashboard SVG from live GitHub data.
Runs in GitHub Actions (stdlib only). Usage:

    GH_TOKEN=... python scripts/github_pulse.py <username> <out.svg> [--mock]

With the default Actions token only public activity is visible. Add a
classic PAT (scopes: repo, read:user) as the repo secret STATS_TOKEN to
include private repos, private contributions and their languages.
"""
import datetime as dt
import json
import math
import os
import random
import sys
import urllib.request
from html import escape

API = "https://api.github.com/graphql"
EXCLUDED_LANGS = {"Jupyter Notebook", "HTML", "CSS", "SCSS", "Makefile", "Procfile",
                  "Mako", "Batchfile", "PowerShell", "Dockerfile", "Shell"}

SANS = "'Segoe UI', Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, Menlo, monospace"
C = dict(bg0="#06080F", bg1="#0C0A1E", card="#0D1222", border="#1D2647", text="#EEF0FF",
         muted="#8A93B5", dim="#4B5578", violet="#A78BFA", cyan="#22D3EE", pink="#F472B6",
         green="#34D399", amber="#FBBF24")


# ------------------------------------------------------------------ data
def gql(token, query, variables):
    req = urllib.request.Request(
        API, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "github-pulse"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"])[:800])
    return body["data"]


Q_USER = """
query($login:String!, $after:String){
  user(login:$login){
    createdAt
    followers{totalCount}
    pullRequests{totalCount}
    repositories(ownerAffiliations:OWNER, isFork:false, first:100, after:$after){
      totalCount
      pageInfo{hasNextPage endCursor}
      nodes{ stargazerCount
        languages(first:12, orderBy:{field:SIZE, direction:DESC}){ edges{ size node{ name color } } } }
    }
  }
}"""

Q_YEAR = """
query($login:String!, $from:DateTime!, $to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from, to:$to){
      totalCommitContributions
      contributionCalendar{ totalContributions weeks{ contributionDays{ date contributionCount } } }
    }
  }
}"""


def fetch(token, login):
    repos, after, user = [], None, None
    while True:
        d = gql(token, Q_USER, {"login": login, "after": after})["user"]
        user = user or d
        repos += d["repositories"]["nodes"]
        pi = d["repositories"]["pageInfo"]
        if not pi["hasNextPage"]:
            break
        after = pi["endCursor"]

    now = dt.datetime.now(dt.timezone.utc)
    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    days, year_totals = {}, {}
    for year in range(created.year, now.year + 1):
        start = max(created, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        cc = gql(token, Q_YEAR, {"login": login, "from": start.isoformat(), "to": end.isoformat()}
                 )["user"]["contributionsCollection"]
        cal = cc["contributionCalendar"]
        year_totals[year] = cal["totalContributions"]
        for w in cal["weeks"]:
            for day in w["contributionDays"]:
                days[day["date"]] = day["contributionCount"]

    langs = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            n = e["node"]["name"]
            if n in EXCLUDED_LANGS:
                continue
            langs.setdefault(n, [0, e["node"]["color"] or "#8A93B5"])
            langs[n][0] += e["size"]

    return dict(
        total=sum(year_totals.values()),
        this_year=year_totals.get(now.year, 0),
        year=now.year,
        since=created.year,
        prs=user["pullRequests"]["totalCount"],
        repos=user["repositories"]["totalCount"],
        stars=sum(r["stargazerCount"] for r in repos),
        followers=user["followers"]["totalCount"],
        days=days,
        langs=langs,
        today=now.date(),
    )


def mock():
    random.seed(7)
    today = dt.date.today()
    days = {}
    for i in range(800):
        d = today - dt.timedelta(days=i)
        days[d.isoformat()] = max(0, int(random.gauss(4, 4))) if random.random() > .25 else 0
    for i in range(9):
        days[(today - dt.timedelta(days=i)).isoformat()] = random.randint(1, 9)
    return dict(total=sum(days.values()), this_year=1342, year=today.year, since=2023, prs=87,
                repos=41, stars=63, followers=120, days=days, today=today,
                langs={"TypeScript": [900000, "#3178c6"], "Python": [720000, "#3572A5"],
                       "JavaScript": [300000, "#f1e05a"], "Dart": [90000, "#00B4AB"],
                       "Java": [60000, "#b07219"], "Go": [20000, "#00ADD8"]})


# ------------------------------------------------------------------ metrics
def streaks(days, today):
    active = {dt.date.fromisoformat(k) for k, v in days.items() if v > 0}
    cur, d = 0, today
    if d not in active:
        d -= dt.timedelta(days=1)  # today isn't over yet
    while d in active:
        cur += 1
        d -= dt.timedelta(days=1)
    best = run = 0
    prev = None
    for d in sorted(active):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return cur, best


def weekly(days, today, weeks=52):
    out = []
    for w in range(weeks - 1, -1, -1):
        end = today - dt.timedelta(days=7 * w)
        out.append((end, sum(days.get((end - dt.timedelta(days=i)).isoformat(), 0) for i in range(7))))
    return out


def fmt(n):
    if n >= 100_000:
        return f"{n / 1000:.0f}k"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


# ------------------------------------------------------------------ render
def smooth_path(pts):
    if len(pts) < 2:
        return ""
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        t = 0.18
        c1 = (p1[0] + (p2[0] - p0[0]) * t, p1[1] + (p2[1] - p0[1]) * t)
        c2 = (p2[0] - (p3[0] - p1[0]) * t, p2[1] - (p3[1] - p1[1]) * t)
        d += f" C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d


def render(s):
    W, H = 1200, 500
    cur, best = streaks(s["days"], s["today"])
    year_ago = s["today"] - dt.timedelta(days=365)
    recent = {k: v for k, v in s["days"].items() if dt.date.fromisoformat(k) > year_ago}
    active = sum(1 for v in recent.values() if v > 0)
    busiest = max(recent.values(), default=0)
    per = 2 * (W - 2 + H - 2)
    o = []
    add = o.append

    add(f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub stats for the last year">
<defs>
  <linearGradient id="fbg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['bg0']}"/><stop offset="1" stop-color="{C['bg1']}"/></linearGradient>
  <linearGradient id="beam" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{C['violet']}"/><stop offset=".5" stop-color="{C['cyan']}"/><stop offset="1" stop-color="{C['pink']}"/></linearGradient>
  <linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C['violet']}" stop-opacity=".45"/><stop offset="1" stop-color="{C['violet']}" stop-opacity="0"/></linearGradient>
  <linearGradient id="line" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{C['violet']}"/><stop offset="1" stop-color="{C['cyan']}"/></linearGradient>
  <pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1" fill="#ffffff" opacity=".05"/></pattern>
</defs>
<style>
  .beam{{stroke-dasharray:{round(per * .18)} {round(per * .82)};animation:run 7s linear infinite}}
  @keyframes run{{to{{stroke-dashoffset:-{per}}}}}
  .up{{animation:up .8s cubic-bezier(.2,.8,.2,1) both}}
  @keyframes up{{from{{opacity:0;transform:translateY(14px)}}}}
  .grow{{transform-box:fill-box;transform-origin:left center;animation:grow 1.4s cubic-bezier(.2,.8,.2,1) both}}
  @keyframes grow{{from{{transform:scaleX(0)}}}}
  .fade{{animation:fade 1.6s .9s ease both}}
  @keyframes fade{{from{{opacity:0}}}}
  .blink{{animation:blink 1.6s ease-in-out infinite}}
  @keyframes blink{{50%{{opacity:.15}}}}
  .ping{{transform-box:fill-box;transform-origin:center;animation:ping 1.8s ease-out infinite}}
  @keyframes ping{{from{{transform:scale(1);opacity:.9}}to{{transform:scale(3.4);opacity:0}}}}
  .lbl{{font-weight:600;font-size:13px;font-family:{MONO};letter-spacing:3px;fill:{C['muted']}}}
  .k{{font-weight:600;font-size:11px;font-family:{MONO};letter-spacing:2px;fill:{C['muted']}}}
  .v{{font-weight:800;font-size:36px;font-family:{SANS};fill:{C['text']}}}
  .s{{font-weight:500;font-size:12px;font-family:{SANS};fill:{C['dim']}}}
  .t{{font-weight:600;font-size:13px;font-family:{SANS};fill:{C['text']}}}
</style>
<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="18" fill="url(#fbg)"/>
<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="18" fill="url(#dots)"/>
<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="18" fill="none" stroke="{C['border']}" stroke-width="1.5"/>
<rect class="beam" x="1" y="1" width="{W - 2}" height="{H - 2}" rx="18" fill="none" stroke="url(#beam)" stroke-width="2" stroke-linecap="round"/>
<text class="lbl" x="34" y="44"><tspan fill="{C['violet']}">02</tspan> // GITHUB_PULSE</text>
<text class="k" x="{W - 58}" y="44" text-anchor="end">LIVE · SYNCED {s['today'].strftime('%d %b %Y').upper()}</text>
<circle cx="{W - 40}" cy="40" r="4" fill="{C['green']}" class="ping"/>
<circle cx="{W - 40}" cy="40" r="4" fill="{C['green']}"/>''')

    # KPI tiles
    kpis = [
        ("CONTRIBUTIONS", fmt(s["total"]), f"all-time · since {s['since']}", C["violet"]),
        (f"IN {s['year']}", fmt(s["this_year"]), "contributions this year", C["cyan"]),
        ("CURRENT STREAK", f"{cur}d", f"best run · {best} days", C["green"]),
        ("PULL REQUESTS", fmt(s["prs"]), "opened all-time", C["pink"]),
        ("REPOSITORIES", fmt(s["repos"]), "owned & building", C["amber"]),
        ("ACTIVE DAYS", fmt(active), f"last 365 · busiest day {busiest}", C["violet"]),
    ]
    tw, gap, ty, th = 178, (W - 68 - 6 * 178) / 5, 68, 112
    for i, (k, v, sub, col) in enumerate(kpis):
        x = 34 + i * (tw + gap)
        add(f'''<g class="up" style="animation-delay:{.1 + i * .08:.2f}s">
  <rect x="{x:.1f}" y="{ty}" width="{tw}" height="{th}" rx="14" fill="{C['card']}" stroke="{C['border']}"/>
  <rect x="{x + 18:.1f}" y="{ty + 18}" width="22" height="3" rx="1.5" fill="{col}"/>
  <text class="k" x="{x + 18:.1f}" y="{ty + 40}">{escape(k)}</text>
  <text class="v" x="{x + 18:.1f}" y="{ty + 80}">{escape(v)}</text>
  <text class="s" x="{x + 18:.1f}" y="{ty + 99}">{escape(sub)}</text>
</g>''')

    # contribution area chart
    px, py, pw, ph = 34, 198, 760, 268
    add(f'''<g class="up" style="animation-delay:.5s">
  <rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="14" fill="{C['card']}" stroke="{C['border']}"/>
  <text class="k" x="{px + 22}" y="{py + 32}">CONTRIBUTIONS · LAST 12 MONTHS</text>
</g>''')
    series = weekly(s["days"], s["today"])
    peak = max(1, max(v for _, v in series))
    cx0, cx1, cy0, cy1 = px + 56, px + pw - 26, py + 62, py + ph - 42
    pts = [(cx0 + i * (cx1 - cx0) / (len(series) - 1), cy1 - v / peak * (cy1 - cy0)) for i, (_, v) in enumerate(series)]
    for f in (0, .5, 1):
        gy = cy1 - f * (cy1 - cy0)
        add(f'<line x1="{cx0}" x2="{cx1}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="{C["border"]}" stroke-dasharray="3 6"/>')
        add(f'<text class="s" x="{cx0 - 12}" y="{gy + 4:.1f}" text-anchor="end">{round(peak * f)}</text>')
    seen = set()
    for i, (end, _) in enumerate(series):
        m = end.strftime("%b")
        if end.day <= 7 and m not in seen and i not in (0,):
            seen.add(m)
            add(f'<text class="s" x="{pts[i][0]:.1f}" y="{cy1 + 24}" text-anchor="middle">{m.upper()}</text>')
    path = smooth_path(pts)
    length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) * 1.08
    add(f'<path class="fade" d="{path} L{cx1} {cy1} L{cx0} {cy1} Z" fill="url(#area)"/>')
    add(f'<path d="{path}" fill="none" stroke="url(#line)" stroke-width="2.5" stroke-linecap="round" '
        f'style="stroke-dasharray:{length:.0f};animation:draw 2.4s .6s cubic-bezier(.4,0,.2,1) both"/>')
    add(f'<style>@keyframes draw{{from{{stroke-dashoffset:{length:.0f}}}}}</style>')
    lx, ly = pts[-1]
    add(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5" fill="{C["cyan"]}" class="ping"/>')
    add(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4.5" fill="{C["cyan"]}" stroke="{C["card"]}" stroke-width="2"/>')
    pi = max(range(len(series)), key=lambda i: series[i][1])
    add(f'<g class="fade"><circle cx="{pts[pi][0]:.1f}" cy="{pts[pi][1]:.1f}" r="4" fill="{C["pink"]}"/>'
        f'<text class="s" x="{pts[pi][0]:.1f}" y="{pts[pi][1] - 12:.1f}" text-anchor="middle" fill="{C["pink"]}" '
        f'style="fill:{C["pink"]}">peak · {series[pi][1]}</text></g>')

    # languages
    lx0, lw = px + pw + 16, W - 34 - (px + pw + 16)
    add(f'''<g class="up" style="animation-delay:.6s">
  <rect x="{lx0}" y="{py}" width="{lw}" height="{ph}" rx="14" fill="{C['card']}" stroke="{C['border']}"/>
  <text class="k" x="{lx0 + 22}" y="{py + 32}">TOP LANGUAGES</text>
</g>''')
    top = sorted(s["langs"].items(), key=lambda kv: -kv[1][0])[:6]
    tot = sum(v[0] for _, v in top) or 1
    bx, bw, by = lx0 + 22, lw - 44, py + 50
    add(f'<clipPath id="bar"><rect x="{bx}" y="{by}" width="{bw}" height="10" rx="5"/></clipPath><g clip-path="url(#bar)">')
    acc = bx
    for name, (size, col) in top:
        w = size / tot * bw
        add(f'<rect class="grow" style="animation-delay:.9s" x="{acc:.1f}" y="{by}" width="{w + .5:.1f}" height="10" fill="{col}"/>')
        acc += w
    add('</g>')
    if not top:
        add(f'<text class="s" x="{bx}" y="{by + 50}">No language data yet</text>')
    for i, (name, (size, col)) in enumerate(top):
        y = by + 40 + i * 30
        pct = size / tot * 100
        add(f'''<g class="up" style="animation-delay:{.8 + i * .08:.2f}s">
  <circle cx="{bx + 5}" cy="{y}" r="5" fill="{col}"/>
  <text class="t" x="{bx + 18}" y="{y + 4.5}">{escape(name)}</text>
  <text class="s" x="{bx + bw}" y="{y + 4.5}" text-anchor="end" style="fill:{C['muted']}">{pct:.1f}%</text>
  <rect x="{bx + 140}" y="{y - 2}" width="{bw - 200}" height="4" rx="2" fill="{C['border']}"/>
  <rect class="grow" style="animation-delay:{1 + i * .08:.2f}s" x="{bx + 140}" y="{y - 2}" width="{max(3, (bw - 200) * pct / 100):.1f}" height="4" rx="2" fill="{col}"/>
</g>''')

    add('</svg>')
    return "\n".join(o)


def main():
    login, out = sys.argv[1], sys.argv[2]
    data = mock() if "--mock" in sys.argv else fetch(os.environ["GH_TOKEN"], login)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(data))
    print(f"wrote {out}: total={data['total']} year={data['this_year']} prs={data['prs']} "
          f"repos={data['repos']} stars={data['stars']} langs={list(data['langs'])[:6]}")


if __name__ == "__main__":
    main()
