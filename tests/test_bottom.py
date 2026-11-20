"""抄底訊號：用假的網路回應跑完整收集 → 廣度 → 事件研究，確認每一段都接得起來、數字對得上手算。
python tests/test_bottom.py
"""
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP
os.environ["BOTTOM_YAHOO_DELAY"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import bottom, store  # noqa: E402

store.DATA_DIR = TMP
bottom.YAHOO_DELAY = 0
DAYS = pd.bdate_range("2004-01-01", periods=1800)
RNG = np.random.default_rng(1)
SPX = 1000 * np.cumprod(1 + RNG.normal(0.0003, 0.01, len(DAYS)))
SPX[900:930] *= np.linspace(1, 0.7, 30)          # 一段急跌
SPX[930:] *= 0.7
CRASH = DAYS[915]
fails = []


def check(name, ok, detail=""):
    print(("✅ " if ok else "❌ ") + name + (f"：{detail}" if detail else ""))
    if not ok:
        fails.append(name)


def yahoo_json(prices, days):
    ts = [int(datetime(d.year, d.month, d.day, 14, tzinfo=timezone.utc).timestamp()) for d in days]
    return json.dumps({"chart": {"result": [{"meta": {"gmtoffset": -18000}, "timestamp": ts,
                       "indicators": {"quote": [{"open": list(prices), "high": list(prices), "low": list(prices), "close": list(prices)}],
                                      "adjclose": [{"adjclose": list(prices)}]}}], "error": None}}).encode()


STOCKS = {f"S{i:03d}": 50 * np.cumprod(1 + RNG.normal(0.0003, 0.015, len(DAYS))) for i in range(300)}
for v in STOCKS.values():
    v[900:930] *= np.linspace(1, 0.6, 30)
    v[930:] *= 0.6
NDXS = {f"N{i:02d}": STOCKS[f"S{i:03d}"] for i in range(60)}
calls = {"pc_daily": 0}


def fake_get(url, headers=None, timeout=30, retries=3):
    if "finance.yahoo.com" in url:
        sym = url.split("/chart/")[1].split("?")[0].replace("%5E", "^").replace("%3D", "=")
        if sym == "DEAD":
            return json.dumps({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}).encode()
        if sym in ("^GSPC", "^NDX"):
            return yahoo_json(SPX, DAYS)
        if sym in ("GC=F", "SI=F"):
            return yahoo_json(SPX[::-1].copy(), DAYS)
        if sym in STOCKS:
            return yahoo_json(STOCKS[sym], DAYS)
        if sym in NDXS:
            return yahoo_json(NDXS[sym], DAYS)
        raise FileNotFoundError(url)
    if url == bottom.URLS["vix"]:
        vix = np.full(len(DAYS), 15.0)
        vix[910:925] = 65.0
        return pd.DataFrame({"DATE": DAYS.strftime("%m/%d/%Y"), "OPEN": vix, "HIGH": vix, "LOW": vix, "CLOSE": vix}).to_csv(index=False).encode()
    if url == bottom.URLS["fg"]:
        fg = np.full(len(DAYS), 50.0)
        fg[912:920] = 5
        return pd.DataFrame({"Date": DAYS.strftime("%Y-%m-%d"), "Fear Greed": fg, "Rating": "x"}).to_csv(index=False).encode()
    if "dataviz.cnn.io" in url:
        raise RuntimeError("HTTP 418")          # CNN 擋掉時要能照常完成
    if url in (bottom.URLS["pc_hist_total"], bottom.URLS["pc_hist_equity"]):
        pc = np.full(len(DAYS), 0.8)
        pc[905:925] = 1.3
        body = "\n".join(f"{d:%m/%d/%Y},100,130,230,{v}" for d, v in zip(DAYS, pc))
        return ("Cboe disclaimer line\nPRODUCT:,TOTAL\nDATE,CALL,PUT,TOTAL,P/C Ratio\n" + body).encode()
    if "market_statistics/daily" in url:
        calls["pc_daily"] += 1
        d = url.split("/daily/")[1][:10]
        if date.fromisoformat(d).weekday() == 0:
            raise FileNotFoundError(url)        # 假裝週一休市
        return json.dumps({"data": {"ratios": [{"name": "TOTAL PUT/CALL RATIO", "value": "0.95"},
                                               {"name": "EQUITY PUT/CALL RATIO", "value": "0.62"}]}}).encode()
    if url == bottom.URLS["sp_members"]:
        rows = [f"S{i:03d},2000-01-01," for i in range(280)] + ["S280,2000-01-01,2004-06-30", "DEAD,2000-01-01,2005-01-01"]
        rows += [f"S{i:03d},2008-01-01," for i in range(281, 300)]      # 後來才加入的
        return ("ticker,start_date,end_date\n" + "\n".join(rows)).encode()
    if url == bottom.URLS["ndx_wiki"]:
        rows = "".join(f"<tr><td>Co {t}</td><td>{t}</td></tr>" for t in NDXS)
        filler = "".join(f"<tr><td>x</td><td>X{i}</td></tr>" for i in range(30))   # 湊到 90 列，像真的表
        return f"<html><table><tr><th>Company</th><th>Ticker</th></tr>{rows}{filler}</table></html>".encode()
    raise FileNotFoundError(url)


# 讓 P/C 每日統計只跑少少幾天
bottom.PC_DAILY_START = date.today() - timedelta(days=12)
res = bottom.collect(lambda d, t: None, lambda: False, with_breadth=True, get=fake_get)
print("\n".join("   " + l for l in res["log"]))
inv = {x["key"]: x for x in bottom.inventory()["series"]}
for k in ("SPX", "NDX", "GOLD", "SILVER", "VIX", "FG", "PC_TOTAL", "PC_EQUITY", "S5FI", "NDFI"):
    check(f"收集 {k}", inv[k]["rows"] > 0, f"{inv[k]['rows']} 天")
check("Yahoo 日期用交易所當地日期", inv["SPX"]["first"] == str(DAYS[0].date()), inv["SPX"]["first"])
check("CNN 失敗不影響恐懼貪婪", inv["FG"]["rows"] == len(DAYS))
pc = bottom.load_series("PC_TOTAL")
check("P/C 每日統計接在歷史檔後面", (pc["value"] == 0.95).sum() > 0 and pc["date"].is_monotonic_increasing)
first_calls = calls["pc_daily"]
bottom.fetch_pc(get=fake_get)
check("P/C 已抓過的日子不重抓（只重抓最近 5 天）", calls["pc_daily"] - first_calls <= 5, f"第二次抓了 {calls['pc_daily'] - first_calls} 天")

# 廣度：手算對照
s5 = bottom.load_series("S5FI").set_index("date")
d = DAYS[1200]
px = pd.DataFrame({t: STOCKS[t] for t in STOCKS}, index=DAYS)
ma = px.rolling(50).mean()
members = [f"S{i:03d}" for i in range(280)] + [f"S{i:03d}" for i in range(281, 300)]   # S280 已出、DEAD 沒資料
want = float((px.loc[d, members] > ma.loc[d, members]).mean() * 100)
check("S5FI 手算一致（只算當時成分股）", abs(s5.loc[d, "value"] - want) < 1e-9, f"{s5.loc[d, 'value']:.2f} vs {want:.2f}")
d0 = DAYS[400]   # 2005 年，S281～S299 還沒加入
want0 = float((px.loc[d0, [f'S{i:03d}' for i in range(280)]] > ma.loc[d0, [f'S{i:03d}' for i in range(280)]]).mean() * 100)
check("S5FI 不算還沒加入的股票", abs(s5.loc[d0, "value"] - want0) < 1e-9)
check("急跌時 S5FI 掉到 10 以下", s5.loc[DAYS[928], "value"] < 10, f"{s5.loc[DAYS[928], 'value']:.1f}")

# 事件研究
r = bottom.study(20)
by = {x["id"]: x for x in r["results"]}
v = by["vix60"]
check("VIX>60 只算一波", v.get("episodes") == 1, str(v.get("episodes")))
e = v["list"][0]
check("訊號日正確", e["date"] == str(DAYS[910].date()), e["date"])
want60 = SPX[911 + 60] / SPX[911] - 1
check("隔天收盤進場、60 天報酬手算一致", abs(e["ret"][60] - round(want60 * 100, 2)) < 1e-9, f"{e['ret'][60]} vs {want60 * 100:.2f}")
check("四大訊號組合有算出來", "error" not in by["four2"] and by["four2"]["episodes"] >= 1)
check("勝過隨機在 0～100", all(0 <= h.get("beat", 50) <= 100 for x in r["results"] if "h" in x for h in x["h"].values()))
check("目前狀態有九項", len(r["now"]) == 9)

# API
from fastapi.testclient import TestClient  # noqa: E402
from app.server import app  # noqa: E402
c = TestClient(app)
check("API 資料清單", c.get("/api/bottom/inventory").status_code == 200)
j = c.post("/api/bottom/study", json={"cooldown": 20})
check("API 比較", j.status_code == 200 and len(j.json()["results"]) == len(bottom.signal_defs()))
check("API 冷卻天數檢查", c.post("/api/bottom/study", json={"cooldown": 999}).status_code == 400)
html = c.get("/").text
check("儀表板有抄底訊號分頁", 'data-t="bot"' in html and 'id="botout"' in html)

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
sys.exit(1 if fails else 0)
