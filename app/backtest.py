"""回測入口：讀資料 → 跑引擎 → 統計。給 API 與最佳化共用。"""
from datetime import datetime, timezone

import pandas as pd

from . import engine, metrics, store
from .instruments import cost_defaults, inst
from .strategies import REGISTRY


def _ts(s, end=False, tz="America/New_York"):
    if not s:
        return None
    d = pd.Timestamp(s, tz=tz)
    if end:
        d = d + pd.Timedelta(days=1)
    return int(d.timestamp())


WARMUP_DAYS = 30      # 指標暖機：資料開頭 30 天只算指標、不交易


def run(req, collect_trades=True):
    """req：source, symbol, strategy, params, minutes, align, start, end, costs(可選)"""
    cls = REGISTRY[req["strategy"]]
    m = store.load(req.get("source", "duka"), req["symbol"])
    costs = cost_defaults(req["symbol"])
    costs.update({k: float(v) for k, v in (req.get("costs") or {}).items() if v is not None and v != ""})
    tz = inst(req["symbol"])["tz"]
    start_ts = _ts(req.get("start"), tz=tz)
    min_start = int(m["ts"][0]) + WARMUP_DAYS * 86400
    if start_ts is None or start_ts < min_start:
        start_ts = min_start
    res = engine.run(cls, req.get("params") or {}, m, costs, minutes=req.get("minutes"),
                     align=req.get("align", "session"), start_ts=start_ts,
                     end_ts=_ts(req.get("end"), end=True, tz=tz), cache_key=(req.get("source", "duka"), req["symbol"]),
                     collect_trades=collect_trades, symbol=req["symbol"])
    res["costs"] = costs
    return res


def full_report(req):
    res = run(req)
    trades, daily = res["trades"], res["daily"]
    summ = metrics.summarize(trades, daily, res["bars_in_pos"], res["n_bars"])
    bh = metrics.buy_hold(daily)
    bh.pop("curve_pct", None)
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo(inst(req["symbol"])["tz"])).strftime("%Y-%m-%d %H:%M")
    tl = []
    for t in trades:
        t = dict(t)
        t["entry_time"] = fmt(t.pop("entry_ts"))
        t["exit_time"] = fmt(t.pop("exit_ts"))
        t.pop("entry_tday", None); t.pop("exit_tday", None)
        t["ret_pct"] = round(t["ret_pct"], 3)
        tl.append(t)
    cls = REGISTRY[req["strategy"]]
    params = cls.all_inputs()
    params.update({k: v for k, v in (req.get("params") or {}).items() if k in params})
    return {"strategy": req["strategy"], "symbol": req["symbol"], "source": req.get("source", "duka"),
            "params": params, "align": req.get("align", "session"), "tz": inst(req["symbol"])["tz"],
            "summary": summ, "buy_hold": bh, "yearly": metrics.yearly(trades),
            "equity": metrics.equity_series(daily), "trades": tl, "costs": res["costs"],
            "minutes": res["minutes"], "range": [metrics.tday_str(daily[0][0]), metrics.tday_str(daily[-1][0])] if daily else None}
