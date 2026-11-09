"""us-index-lab API + 儀表板"""
import base64
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import backtest, dukascopy, jobs, mt5import, optimize, store
from .instruments import INSTRUMENTS, cost_defaults
from .strategies import REGISTRY

PASSWORD = os.getenv("LAB_PASSWORD", "")
HERE = os.path.dirname(__file__)
app = FastAPI(title="us-index-lab")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD and request.url.path != "/healthz":
        h = request.headers.get("authorization", "")
        ok = False
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("需要密碼", status_code=401, headers={"WWW-Authenticate": 'Basic realm="us-index-lab"'})
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "dashboard.html"), encoding="utf-8") as fh:
        return fh.read()


@app.get("/api/meta")
def meta():
    return {
        "instruments": {k: {"name": v["name"], "duka": v["duka"], "costs": cost_defaults(k)} for k, v in INSTRUMENTS.items()},
        "strategies": {k: {"desc": c.desc, "minutes": c.minutes, "inputs": c.all_inputs(), "doc": c.all_doc()}
                       for k, c in REGISTRY.items()},
        "objectives": optimize.OBJECTIVES,
        "data": store.inventory(),
        "workers": optimize.WORKERS,
        "max_combos": optimize.MAX_COMBOS,
    }


# ------------------------------------------------------------------ 資料
@app.post("/api/download")
async def download(body: dict):
    sym = body.get("symbol")
    if sym not in INSTRUMENTS:
        raise HTTPException(400, "未知商品")
    if jobs.running("download"):
        raise HTTPException(409, "已有下載在跑，等它結束再開新的")
    years = float(body.get("years") or 3)
    end = date.fromisoformat(body["end"]) if body.get("end") else datetime.now(timezone.utc).date()
    start = date.fromisoformat(body["start"]) if body.get("start") else end - timedelta(days=int(years * 365.25))
    job = jobs.start("download", lambda p, s: dukascopy.download(sym, start, end, p, s),
                     label=f"Dukascopy {sym} {start}～{end}")
    return jobs.public(job)


@app.get("/api/duka_test")
async def duka_test(symbol: str = "US500"):
    return await run_in_threadpool(dukascopy.diagnose, symbol)


@app.post("/api/import_mt5")
async def import_mt5(file: UploadFile = File(...), broker: str = Form(...), symbol: str = Form(...)):
    if symbol not in INSTRUMENTS:
        raise HTTPException(400, "未知商品")
    raw = await file.read()
    try:
        return await run_in_threadpool(mt5import.import_csv, raw, broker, symbol)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/delete_data")
def delete_data(body: dict):
    store.delete(body["source"], body["symbol"])
    from . import bars
    bars.clear_cache()
    return {"ok": True}


@app.get("/api/compare")
async def compare(symbol: str, a: str = "duka", b: str = ""):
    try:
        return await run_in_threadpool(mt5import.compare, symbol, a, b)
    except Exception as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ 回測
@app.post("/api/backtest")
async def run_backtest(body: dict):
    if body.get("strategy") not in REGISTRY:
        raise HTTPException(400, "未知策略")
    try:
        t0 = time.time()
        r = await run_in_threadpool(backtest.full_report, body)
        r["elapsed"] = round(time.time() - t0, 2)
        return r
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))


@app.post("/api/optimize")
async def run_optimize(body: dict):
    if jobs.running("optimize"):
        raise HTTPException(409, "已有最佳化在跑，等它結束或先停止")
    req = body["req"]
    grid = {k: v for k, v in (body.get("grid") or {}).items() if v}
    if not grid:
        raise HTTPException(400, "至少勾選一個要掃的參數")
    _, _, total = optimize.combos(grid)
    if total > optimize.MAX_COMBOS:
        raise HTTPException(400, f"組合數 {total:,} 超過上限 {optimize.MAX_COMBOS:,}")
    job = jobs.start("optimize", lambda p, s: optimize.run(
        req, grid, split=float(body.get("split", 0.67)), objective=body.get("objective", "net_mdd"),
        min_trades=int(body.get("min_trades", 30)), progress=p, stop_flag=s),
        label=f"{req.get('strategy')} {req.get('symbol')} {total} 組")
    return jobs.public(job)


@app.get("/api/jobs")
def list_jobs():
    return [jobs.public(j, with_result=False) for j in sorted(jobs.JOBS.values(), key=lambda j: -j["started"])]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.JOBS.get(jid)
    if not j:
        raise HTTPException(404, "找不到這個工作（服務重啟過的話工作紀錄會清空）")
    return jobs.public(j)


@app.post("/api/jobs/{jid}/stop")
def stop_job(jid: str):
    j = jobs.JOBS.get(jid)
    if j:
        j["stop"] = True
    return {"ok": True}


# ------------------------------------------------------------------ 參數組合存檔
def _preset_path():
    return os.path.join(store.DATA_DIR, "presets.json")


def _presets():
    try:
        with open(_preset_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


@app.get("/api/presets")
def presets():
    return _presets()


@app.post("/api/presets")
def save_preset(body: dict):
    ps = [p for p in _presets() if p.get("name") != body.get("name")]
    body["saved_at"] = datetime.now(timezone.utc).isoformat()[:19]
    ps.insert(0, body)
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(_preset_path(), "w", encoding="utf-8") as fh:
        json.dump(ps[:200], fh, ensure_ascii=False, indent=1)
    return {"ok": True}


@app.post("/api/presets/delete")
def delete_preset(body: dict):
    ps = [p for p in _presets() if p.get("name") != body.get("name")]
    with open(_preset_path(), "w", encoding="utf-8") as fh:
        json.dump(ps, fh, ensure_ascii=False, indent=1)
    return {"ok": True}
