#!/usr/bin/env python3
"""Gera stats.json com números reais (npm, Packagist, GitHub). Só stdlib.

Uso:  GITHUB_TOKEN=... python3 build_stats.py [saida.json]
Sem GITHUB_TOKEN o histórico de stars é omitido (a API de stargazers exige auth).
"""
import collections, datetime as dt, json, os, sys, urllib.parse, urllib.request

GH_USERS = ["jeanpfs", "lab123"]       # contas cujos repos somam stars
NPM_USER = "jean.pierre"
PACKAGIST_VENDOR = "lab123"
HIDE = {"lab123/odin", "@laberry/fab-material", "fab-material"}  # internos/sem descrição
TOKEN = os.environ.get("GITHUB_TOKEN")
NOW = dt.datetime.now(dt.timezone.utc)


def get(url, accept=None, auth=False):
    h = {"User-Agent": "stats-builder"}
    if accept:
        h["Accept"] = accept
    if auth and TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.load(r)


def pages(url, **kw):
    out, p = [], 1
    while True:
        d = get(f"{url}{'&' if '?' in url else '?'}per_page=100&page={p}", **kw)
        out += d
        if len(d) < 100:
            return out
        p += 1


def months_back(n=12):
    """Últimos n meses 'YYYY-MM', do mais antigo ao atual."""
    y, m, out = NOW.year, NOW.month, []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


MONTHS = months_back()


def cumulative(total, per_month):
    """Série acumulada terminando em `total`, a partir dos incrementos mensais."""
    run, series = total, []
    for mo in reversed(MONTHS):
        series.append(run)
        run -= per_month.get(mo, 0)
    return series[::-1]


# ---- npm ----
search = get(f"https://registry.npmjs.org/-/v1/search?text=maintainer:{NPM_USER}&size=100")
npm_pkgs, npm_month, npm_inc = [], 0, collections.Counter()
start = f"{MONTHS[0]}-01"
for o in search["objects"]:
    name = o["package"]["name"]
    q = urllib.parse.quote(name, safe="")
    try:  # pacote recém-publicado: a API de downloads responde 404 até ter dados
        last = get(f"https://api.npmjs.org/downloads/point/last-month/{q}")["downloads"]
        total = get(f"https://api.npmjs.org/downloads/point/2015-01-01:{NOW:%Y-%m-%d}/{q}")["downloads"]
        daily = get(f"https://api.npmjs.org/downloads/range/{start}:{NOW:%Y-%m-%d}/{q}")["downloads"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        last, total, daily = 0, 0, []
    for d in daily:
        npm_inc[d["day"][:7]] += d["downloads"]
    npm_month += last
    npm_pkgs.append({"name": name, "desc": o["package"].get("description") or "", "registry": "npm",
                     "downloads": total, "stars": 0, "version": o["package"]["version"]})
npm_total = sum(p["downloads"] for p in npm_pkgs)

# ---- Packagist ----
comp_pkgs = []
for n in get(f"https://packagist.org/packages/list.json?vendor={PACKAGIST_VENDOR}")["packageNames"]:
    p = get(f"https://packagist.org/packages/{n}.json")["package"]
    desc = p["description"] or ""
    comp_pkgs.append({"name": n, "desc": desc, "registry": "composer",
                      "downloads": p["downloads"]["total"], "stars": p["favers"],
                      "month": p["downloads"]["monthly"], "abandoned": bool(p.get("abandoned"))})
comp_total = sum(p["downloads"] for p in comp_pkgs)
comp_month = sum(p["month"] for p in comp_pkgs)

# ---- GitHub ----
repos = []
for u in GH_USERS:
    repos += [r for r in pages(f"https://api.github.com/users/{u}/repos?type=owner", auth=True) if not r["fork"]]
stars_total = sum(r["stargazers_count"] for r in repos)
star_inc, star_history = collections.Counter(), None


def stars_from_previous(path):
    """Sem acesso aos stargazers (token da Action só vê este repo): reaproveita o
    histórico gravado, atualizando o mês corrente (ou avançando uma posição na virada)."""
    try:
        prev = json.load(open(path))
        hist = prev["history"]["stars"]
        prev_month = prev["updatedAt"][:7]
    except (OSError, KeyError, ValueError):
        return None
    steps = (NOW.year - int(prev_month[:4])) * 12 + NOW.month - int(prev_month[5:7])
    hist = hist + [hist[-1]] * max(0, min(steps, 12))
    hist = hist[-12:]
    hist[-1] = stars_total
    return hist


try:
    if not TOKEN:
        raise PermissionError("sem token")
    for r in repos:
        if r["stargazers_count"]:
            for s in pages(f"https://api.github.com/repos/{r['full_name']}/stargazers",
                           accept="application/vnd.github.star+json", auth=True):
                star_inc[s["starred_at"][:7]] += 1
    star_history = cumulative(stars_total, star_inc)
except (PermissionError, urllib.error.HTTPError):
    star_history = stars_from_previous(sys.argv[1] if len(sys.argv) > 1 else "stats.json")
by_name = {r["name"]: r for r in repos}
for p in npm_pkgs:  # stars do repo homônimo
    p["stars"] = by_name.get(p["name"].split("/")[-1], {}).get("stargazers_count", 0)

# ---- releases (npm + GitHub) ----
releases = []
for p in npm_pkgs:
    if p["name"] in HIDE:
        continue
    times = get(f"https://registry.npmjs.org/{urllib.parse.quote(p['name'], safe='@')}")["time"]
    for v, t in times.items():
        if v not in ("created", "modified"):
            releases.append({"name": p["name"], "tag": "v" + v, "date": t[:10],
                             "url": f"https://www.npmjs.com/package/{p['name']}/v/{v}"})
gh_asset_downloads = 0  # downloads dos binários anexados às releases (apps)
for r in repos:
    all_rel = pages(f"https://api.github.com/repos/{r['full_name']}/releases", auth=True)
    gh_asset_downloads += sum(a["download_count"] for rel in all_rel for a in rel["assets"])
    for rel in all_rel[:3]:
        releases.append({"name": r["name"], "tag": rel["tag_name"], "date": rel["published_at"][:10], "url": rel["html_url"]})
seen, uniq = set(), []
for r in sorted(releases, key=lambda x: x["date"], reverse=True):
    k = (r["name"], r["tag"].lstrip("v"))
    if k not in seen:
        seen.add(k)
        uniq.append(r)

recent = sorted((r for r in repos if r["name"] not in GH_USERS and not r["name"].endswith(".github.io")
                 and not r["archived"]), key=lambda r: r["created_at"], reverse=True)[:6]

out = {
    "npm": {"total": npm_total, "month": npm_month},
    "packagist": {"total": comp_total, "month": comp_month},
    "stars": {"total": stars_total},
    "githubAssets": {"total": gh_asset_downloads},
    "updatedAt": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "packages": [p for p in sorted(npm_pkgs + comp_pkgs, key=lambda p: -p["downloads"])
                 if p["name"] not in HIDE and p["desc"] and not p.get("abandoned")][:5],
    "releases": uniq[:6],
    "repos": [{"name": r["name"], "desc": r["description"] or "", "stars": r["stargazers_count"],
               "created": r["created_at"][:10], "url": r["html_url"]} for r in recent],
    "history": {"npm": cumulative(npm_total, npm_inc)},
}
if star_history:
    out["history"]["stars"] = star_history
path = sys.argv[1] if len(sys.argv) > 1 else "stats.json"
with open(path, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(f"{path}: npm {npm_total} (+{npm_month}/mês) · packagist {comp_total} · stars {stars_total}")

# mantém o fallback embutido no index.html (usado quando o fetch falha, ex.: file://)
html_path = os.path.join(os.path.dirname(os.path.abspath(path)), "index.html")
if os.path.exists(html_path):
    import re
    html = open(html_path, encoding="utf-8").read()
    blob = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    new = re.sub(r"/\*FALLBACK\*/.*?/\*END\*/", lambda m: f"/*FALLBACK*/const FALLBACK = {blob};/*END*/", html, flags=re.S)
    open(html_path, "w", encoding="utf-8").write(new)
