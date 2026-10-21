#!/usr/bin/env python3
"""
Renders a minimal animated activity card (light + dark SVG) from live GitHub data.
Runs in GitHub Actions (stdlib only). Usage:

    GH_TOKEN=... python scripts/github_pulse.py <username> <out_dir> [--mock]

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
THEMES = {
    "light": dict(text="#1F2328", muted="#59636E", dim="#8C959F", rule="#D1D9E0", accent="#1A7F37"),
    "dark": dict(text="#F0F6FC", muted="#9198A1", dim="#656C76", rule="#3D444D", accent="#3FB950"),
}
GH_SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
GH_MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"


def smooth_path(pts):
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        t = 0.16
        c1 = (p1[0] + (p2[0] - p0[0]) * t, min(p1[1] + (p2[1] - p0[1]) * t, BASE_Y))
        c2 = (p2[0] - (p3[0] - p1[0]) * t, min(p2[1] - (p3[1] - p1[1]) * t, BASE_Y))
        d += f" C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d


W, COL, BASE_Y = 1000, 190, 238


def render(s, t):
    cur, best = streaks(s["days"], s["today"])
    year_ago = s["today"] - dt.timedelta(days=365)
    recent = {k: v for k, v in s["days"].items() if dt.date.fromisoformat(k) > year_ago}
    active = sum(1 for v in recent.values() if v > 0)

    o = []
    add = o.append
    stats = [
        (fmt(s["total"]), f"contributions since {s['since']}"),
        (fmt(s["prs"]), "pull requests"),
        (f"{cur}", f"day streak · best {best}"),
        (fmt(active), "active days · 12 months"),
    ]

    # chart geometry
    series = weekly(s["days"], s["today"])
    peak = max(1, max(v for _, v in series))
    x0, x1, y0 = COL, W - 6, 136
    pts = [(x0 + i * (x1 - x0) / (len(series) - 1), BASE_Y - v / peak * (BASE_Y - y0)) for i, (_, v) in enumerate(series)]
    path = smooth_path(pts)
    length = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) * 1.1

    # languages
    top = sorted(s["langs"].items(), key=lambda kv: -kv[1][0])[:5]
    tot = sum(v[0] for _, v in top) or 1
    shades = [1, .62, .38, .22, .12]
    LY = 318
    H = LY + 66

    add(f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub activity">
<style>
  .label{{font-family:{GH_MONO};font-size:12px;letter-spacing:1.5px;fill:{t['dim']}}}
  .note{{font-family:{GH_MONO};font-size:11.5px;fill:{t['dim']}}}
  .num{{font-family:{GH_SANS};font-size:28px;font-weight:600;letter-spacing:-.5px;fill:{t['text']}}}
  .cap{{font-family:{GH_SANS};font-size:13.5px;fill:{t['muted']}}}
  .lang{{font-family:{GH_SANS};font-size:14px;fill:{t['text']}}}
  .pct{{font-family:{GH_MONO};font-size:12px;fill:{t['dim']}}}
  .in{{animation:in .9s cubic-bezier(.2,.7,.2,1) both}}
  @keyframes in{{from{{opacity:0;transform:translateY(8px)}}}}
  .line{{fill:none;stroke:{t['text']};stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;
         stroke-dasharray:{length:.0f};animation:draw 2.6s .4s cubic-bezier(.4,0,.2,1) both}}
  @keyframes draw{{from{{stroke-dashoffset:{length:.0f}}}}}
  .grow{{transform-box:fill-box;transform-origin:left center;animation:grow 1.2s .6s cubic-bezier(.2,.7,.2,1) both}}
  @keyframes grow{{from{{transform:scaleX(0)}}}}
  .ring{{fill:none;stroke:{t['accent']};transform-box:fill-box;transform-origin:center;animation:ring 2.4s ease-out infinite}}
  @keyframes ring{{from{{opacity:.8;transform:scale(1)}}to{{opacity:0;transform:scale(2.6)}}}}
</style>
<text class="label" x="0" y="44">ACTIVITY</text>
<text class="note" x="0" y="66">live · {s['today'].strftime('%d %b').lower()}</text>""")

    cw = (W - COL) / 4
    for i, (n, cap) in enumerate(stats):
        x = COL + i * cw
        add(f'<g class="in" style="animation-delay:{i * .08:.2f}s"><text class="num" x="{x:.1f}" y="52">{escape(n)}</text>'
            f'<text class="cap" x="{x:.1f}" y="76">{escape(cap)}</text></g>')

    add(f'<line x1="{x0}" x2="{x1}" y1="{BASE_Y + .5}" y2="{BASE_Y + .5}" stroke="{t["rule"]}"/>')
    add(f'<path class="line" d="{path}"/>')
    seen = set()
    for i, (end, _) in enumerate(series):
        m = end.strftime("%b")
        if end.day <= 7 and m not in seen and 0 < i < len(series) - 2:
            seen.add(m)
            add(f'<text class="note" x="{pts[i][0]:.1f}" y="{BASE_Y + 22}" text-anchor="middle">{m.lower()}</text>')
    lx, ly = pts[-1]
    add(f'<circle class="ring" cx="{lx:.1f}" cy="{ly:.1f}" r="3.5"/><circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{t["accent"]}"/>')
    pi = max(range(len(series)), key=lambda i: series[i][1])
    add(f'<text class="note in" style="animation-delay:2.4s" x="{pts[pi][0]:.1f}" y="{pts[pi][1] - 10:.1f}" text-anchor="middle">{series[pi][1]}/wk</text>')
    add(f'<text class="note" x="0" y="{BASE_Y + 22}">12 months</text>')

    add(f'<line x1="0" x2="{W}" y1="{LY - 40.5}" y2="{LY - 40.5}" stroke="{t["rule"]}"/>')
    add(f'<text class="label" x="0" y="{LY}">LANGUAGES</text>')
    bw = W - COL
    add(f'<clipPath id="lb"><rect x="{COL}" y="{LY - 10}" width="{bw}" height="6" rx="3"/></clipPath><g clip-path="url(#lb)">')
    acc = COL
    for i, (name, (size, _)) in enumerate(top):
        w = size / tot * bw
        add(f'<rect class="grow" x="{acc:.1f}" y="{LY - 10}" width="{w + .5:.1f}" height="6" fill="{t["text"]}" fill-opacity="{shades[i]}"/>')
        acc += w
    add('</g>')
    x = COL
    for i, (name, (size, _)) in enumerate(top):
        pct = f"{size / tot * 100:.0f}%"
        add(f'<g class="in" style="animation-delay:{.8 + i * .08:.2f}s"><circle cx="{x + 4}" cy="{LY + 23}" r="4" fill="{t["text"]}" fill-opacity="{shades[i]}"/>'
            f'<text class="lang" x="{x + 14}" y="{LY + 28}">{escape(name)}</text>'
            f'<text class="pct" x="{x + 22 + len(name) * 7.9:.0f}" y="{LY + 28}">{pct}</text></g>')
        x += 22 + len(name) * 7.9 + len(pct) * 7.4 + 28
    add(f'<line x1="0" x2="{W}" y1="{H - .5}" y2="{H - .5}" stroke="{t["rule"]}"/>')
    add("</svg>")
    return "\n".join(o)


def main():
    login, out_dir = sys.argv[1], sys.argv[2]
    data = mock() if "--mock" in sys.argv else fetch(os.environ["GH_TOKEN"], login)
    os.makedirs(out_dir, exist_ok=True)
    for name, theme in THEMES.items():
        with open(os.path.join(out_dir, f"pulse-{name}.svg"), "w", encoding="utf-8") as f:
            f.write(render(data, theme))
    print(f"wrote {out_dir}: total={data['total']} year={data['this_year']} prs={data['prs']} "
          f"repos={data['repos']} stars={data['stars']} langs={list(data['langs'])[:6]}")


if __name__ == "__main__":
    main()
