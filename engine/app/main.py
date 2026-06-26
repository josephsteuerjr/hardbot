"""
FastAPI-оркестратор движка «Контур · Выезды».

Один лёгкий сервис (помещается на 1 vCPU / 3 ГБ): отдаёт дашборд + JSON-API
(скоринг, маршрут, аналитика). Источник людей:
  - реальная БД бота, если задан HARDBOT_DB (SQLite, на сервере/машине владельца);
  - синтетика (демо), если БД нет — ПД при этом не участвуют.

Скоринг — ЕДИНЫЙ модуль бота hardbot/scoring.py (score_items); геокод/маршрут —
geo/routing. Дубля скоринга (бывш. build_demo.score) больше нет — один источник истины.
"""
from __future__ import annotations
import hmac, os, sys, sqlite3
from collections import Counter
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(ENGINE.parent / "hardbot"))   # единый скоринг/газеттир/util
import geo, routing                      # noqa: E402
import scoring                           # noqa: E402  — ЕДИНЫЙ скоринг (был build_demo.score)
from build_demo import gen               # noqa: E402  — синтетика
from util import num as _money           # noqa: E402  — единый разбор чисел (был локальный _money)

app = FastAPI(title="Контур · Движок выездов", version="0.1")
WEB = ENGINE / "web"

# --- ИБ: auth-слой на API движка (defense-in-depth поверх «порт только 127.0.0.1») ---
# Реальные ПД (HARDBOT_DB) НИКОГДА не отдаём без токена. Синтетика без токена — открыта
# (постоянная демо-витрина, ПД там нет). Токен — env ENGINE_API_TOKEN, сравнение constant-time.
ENGINE_API_TOKEN = os.getenv("ENGINE_API_TOKEN", "").strip()
_REAL_DB = bool(os.getenv("HARDBOT_DB") and Path(os.getenv("HARDBOT_DB", "")).exists())
# CORS на tiles-прокси: по умолчанию НЕ ставим заголовок (тайлы грузятся same-origin через
# Caddy). Кросс-ориджин — только если явно задан CORS_ALLOW_ORIGIN (был открытый «*»).
TILES_CORS_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "").strip()

def _enforce_startup_safety() -> None:
    """ИБ-гард старта (defense-in-depth поверх токен-auth). Реальную БД (ПД) НЕЛЬЗЯ:
    (1) поднимать без ENGINE_API_TOKEN; (2) публиковать на 0.0.0.0 — только 127.0.0.1
    (наружу — через Caddy/SSH-туннель). Синтетика (нет HARDBOT_DB) — без ограничений."""
    if not _REAL_DB:
        return
    if not ENGINE_API_TOKEN:
        raise RuntimeError(
            "Движок с реальной БД (HARDBOT_DB) запущен БЕЗ ENGINE_API_TOKEN — старт запрещён. "
            "Задай ENGINE_API_TOKEN: ПД должников не отдаём без аутентификации.")
    argv = " ".join(sys.argv)
    if (os.getenv("ENGINE_HOST", "") == "0.0.0.0"
            or "--host 0.0.0.0" in argv or "--host=0.0.0.0" in argv):
        raise RuntimeError(
            "Движок с реальной БД нельзя публиковать на 0.0.0.0 — bind ТОЛЬКО 127.0.0.1 "
            "(наружу — через Caddy/SSH-туннель). Убери --host 0.0.0.0.")


_enforce_startup_safety()


def _engine_auth(x_engine_token: str = Header(default="")) -> None:
    if not _REAL_DB and not ENGINE_API_TOKEN:
        return                                      # демо без ПД — открыто
    if not ENGINE_API_TOKEN or not hmac.compare_digest(x_engine_token, ENGINE_API_TOKEN):
        raise HTTPException(status_code=401, detail="engine token required")


def _load_people():
    """(люди, источник). Реальная БД по HARDBOT_DB или синтетика."""
    dbp = os.getenv("HARDBOT_DB")
    if dbp and Path(dbp).exists():
        con = sqlite3.connect(dbp); con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT * FROM clients").fetchall()
        finally:
            con.close()
        ppl = []
        for r in rows:
            d = dict(r)
            ppl.append({
                "rep": d.get("contract_id"), "client": d.get("client"),
                "city": d.get("city"), "address": d.get("address"),
                "debt": _money(d.get("debt_today") or d.get("debt_total")),
                "visits": int(d.get("visits_count") or 0),
                "result": d.get("overall_result"), "who": d.get("who_at_address"),
                "location": d.get("client_location"),
                "employee": d.get("responsible"), "partner": d.get("partner"),
            })
        return ppl, "db"
    return gen(150), "synthetic"


def _enrich(people, weights, audience):
    scored = scoring.score_items(people, weights, audience)   # чистое ядро → новый список
    out = []
    for p in scored:
        g = geo.geocode_jittered(p.get("city"), p.get("address"), p.get("rep") or "")
        if g:
            p["lat"], p["lon"] = g[0], g[1]
            out.append(p)
        else:
            p["lat"] = p["lon"] = None
    return out


_KEYS = ("rep", "client", "city", "partner", "debt", "visits", "lat", "lon",
         "score", "mode", "reason", "dropped", "debt_s", "cluster_s", "fresh_s", "adj")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "kontur-engine"}


@app.get("/api/points")
def api_points(audience: str = "courier", wd: int = 50, wc: int = 30, wf: int = 20,
               _auth: None = Depends(_engine_auth)):
    ppl, src = _load_people()
    pts = _enrich(ppl, {"debt": wd, "cluster": wc, "fresh": wf}, audience)
    return {"source": src, "count": len(pts),
            "points": [{k: p.get(k) for k in _KEYS} for p in pts]}


@app.get("/api/route")
def api_route(city: str = "", audience: str = "courier",
              wd: int = 50, wc: int = 30, wf: int = 20, limit: int = 10,
              _auth: None = Depends(_engine_auth)):
    ppl, _ = _load_people()
    pts = _enrich(ppl, {"debt": wd, "cluster": wc, "fresh": wf}, audience)
    active = [p for p in pts if not p["dropped"]]
    if not active:
        return {"city": city, "stops": [], "line": [], "km": 0, "minutes": 0}
    if not city:
        city = Counter(p["city"] for p in active).most_common(1)[0][0]
    day = sorted([p for p in active if p["city"] == city],
                 key=lambda x: x["score"], reverse=True)[:limit]
    rt = routing.optimize(day, start=geo.GAZ.get(geo._norm(city)))
    return {"city": city, "stops": [day[i]["rep"] for i in rt["order"]], **rt}


@app.get("/api/analytics")
def api_analytics(audience: str = "courier", _auth: None = Depends(_engine_auth)):
    ppl, src = _load_people()
    pts = _enrich(ppl, {"debt": 50, "cluster": 30, "fresh": 20}, audience)
    active = [p for p in pts if not p["dropped"]]
    return {"source": src, "people": len(ppl), "active": len(active),
            "dropped": len(pts) - len(active),
            "debt_active": sum(p["debt"] for p in active),
            "mode_split": dict(Counter(p["mode"] for p in active)),
            "by_city": [{"name": c, "count": n}
                        for c, n in Counter(p["city"] for p in active).most_common()]}


@app.get("/", response_class=HTMLResponse)
def home():
    f = WEB / "dashboard.html"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return "<h1>Запусти: python engine/build_demo.py</h1>"


@app.get("/api/plan")
def api_plan(agents: int = 2, city: str = "", audience: str = "courier",
             wd: int = 50, wc: int = 30, wf: int = 20, limit: int = 18,
             _auth: None = Depends(_engine_auth)):
    """Раздача дня на бригаду из N выездников."""
    import planner
    ppl, _ = _load_people()
    pts = _enrich(ppl, {"debt": wd, "cluster": wc, "fresh": wf}, audience)
    active = [p for p in pts if not p["dropped"]]
    if not active:
        return {"city": city, "agents": [], "n": 0}
    if not city:
        city = Counter(p["city"] for p in active).most_common(1)[0][0]
    day = sorted([p for p in active if p["city"] == city],
                 key=lambda x: x["score"], reverse=True)[:limit]
    res = planner.plan(day, agents, start=geo.GAZ.get(geo._norm(city)))
    return {"city": city, **res}


# --- PMTiles range-прокси (для локального запуска: вектор-слой тянется с сервера) ---
from fastapi import Request as _Request          # noqa: E402
from fastapi.responses import Response as _Response  # noqa: E402
import urllib.request as _urlreq                 # noqa: E402

_PMTILES_UPSTREAM = os.getenv(
    "PMTILES_UPSTREAM", "https://kontur.166.88.185.90.nip.io/tiles/basemap.pmtiles")


@app.get("/tiles/basemap.pmtiles")
def tiles_proxy(request: _Request):
    """Прокси PMTiles по Range. Локально дашборд берёт вектор отсюда (same-origin),
    а байты идут с сервера. На сервере /tiles раздаёт Caddy напрямую — прокси не нужен."""
    rng = request.headers.get("range")
    req = _urlreq.Request(_PMTILES_UPSTREAM)
    if rng:
        req.add_header("Range", rng)
    try:
        with _urlreq.urlopen(req, timeout=30) as r:
            body = r.read()
            status = getattr(r, "status", 200) or 200
            h = {"Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
            if TILES_CORS_ORIGIN:                     # CORS сужен: «*» убран, только явный origin
                h["Access-Control-Allow-Origin"] = TILES_CORS_ORIGIN
            cr = r.headers.get("Content-Range")
            if cr:
                h["Content-Range"] = cr
        return _Response(content=body, status_code=status, headers=h)
    except Exception as e:
        return _Response(content=str(e).encode(), status_code=502)
