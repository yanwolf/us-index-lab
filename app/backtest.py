"""回測入口：讀資料 → 跑引擎 → 統計。給 API 與最佳化共用。"""
from datetime import datetime, timezone

import pandas as pd

from . import engine, metrics, store
from .instruments import cost_defaults
from .strategies import REGISTRY


def _ts(s, end=False):
    if not s:
        return None
    d = pd.Timestamp(s, tz="America/New_York")
    if end:
        d = d + pd.Timedelta(days=1)
    return int(d.timestamp())


def run(req, collect_trades=True):
    """req：source, symbol, strategy, params, minutes, align, start, end, costs(可選)"""
    cls = REGISTRY[req["strategy"]]
    m = store.load(req.get("source", "duka"), req["symbol"])
    costs = cost_defaults(req["symbol"])
    costs.update({k: float(v) for k, v in (req.get("costs") or {}).items() if v is not None and v != ""})
    res = engine.run(cls, req.get("params") or {}, m, costs, minutes=req.get("minutes"),
                     align=req.get("align", "session"), start_ts=_ts(req.get("start")),
                     end_ts=_ts(req.get("end"), end=True), cache_key=(req.get("source", "duka"), req["symbol"]),
                     collect_trades=collect_trades)
    res["costs"] = costs
    return res


def full_report(req):
    res = run(req)
    trades, daily = res["trades"], res["daily"]
    summ = metrics.summarize(trades, daily, res["bars_in_pos"], res["n_bars"])
    bh = metrics.buy_hold(daily)
    bh.pop("curve_pct", None)
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
    tl = []
    for t in trades:
        t = dict(t)
        t["entry_time"] = fmt(t.pop("entry_ts"))
        t["exit_time"] = fmt(t.pop("exit_ts"))
        t["ret_pct"] = round(t["ret_pct"], 3)
        tl.append(t)
    return {"summary": summ, "buy_hold": bh, "yearly": metrics.yearly(trades),
            "equity": metrics.equity_series(daily), "trades": tl, "costs": res["costs"],
            "minutes": res["minutes"], "range": [metrics.tday_str(daily[0][0]), metrics.tday_str(daily[-1][0])] if daily else None}
