"""商品設定：交易所時區、交易日切換、主時段、資料代碼、成本預設值。

每個商品用自己交易所的當地時間處理（夏令時間自動換算）：
  tz         時區
  day_roll   交易日切換點（當地 HHMM）：這個時間之後的交易歸到「下一個交易日」
             （期貨慣例：美股 18:00、日經 / 恆指 夜盤歸隔天）
  rth        主時段（當地 HHMM 起訖），其餘時間算延長／夜盤時段
  eth_start  延長／夜盤開盤時間，夜盤 K 棒從這裡起算（MC 慣例：每個時段開盤重新切 K）
             CloseS 就是在「主時段 ↔ 延長時段」之間交替，類比台指日盤／夜盤
週末出場不看鐘點，改看資料：每週最後一分鐘之前 WKX 分鐘起出場、停止進場（見 engine.py）。

成本都是「指數點」：spread 是買賣價差（整筆來回付一次），slippage 每邊各算一次。
隔夜利息用年化 % 計（正數 = 成本），每跨一個交易日收一次。
預設值只是常見水準，實際請用 tools/mt5_probe.py 從要下單的券商撈出來校準。
"""

INSTRUMENTS = {
    "US500": {
        "name": "標普 500", "duka": "USA500IDXUSD", "price_range": (1000, 20000),
        "tz": "America/New_York", "day_roll": 1800, "rth": (930, 1600), "eth_start": 1600, "rth_name": "正規盤", "eth_name": "延長盤",
        "spread": 0.5, "slippage": 0.2, "commission": 0.0, "swap_long_pct": 6.5, "swap_short_pct": 0.0,
        "usd_per_point": 1.0,
    },
    "NAS100": {
        "name": "那斯達克 100", "duka": "USATECHIDXUSD", "price_range": (3000, 80000),
        "tz": "America/New_York", "day_roll": 1800, "rth": (930, 1600), "eth_start": 1600, "rth_name": "正規盤", "eth_name": "延長盤",
        "spread": 1.5, "slippage": 0.5, "commission": 0.0, "swap_long_pct": 6.5, "swap_short_pct": 0.0,
        "usd_per_point": 1.0,
    },
    "JP225": {
        # 日經 225：大阪期貨日盤 08:45–15:45、夜盤 17:00–翌日 06:00（夜盤歸隔天），和台指結構最像
        "name": "日經 225", "duka": "JPNIDXJPY", "price_range": (10000, 120000),
        "tz": "Asia/Tokyo", "day_roll": 1600, "rth": (845, 1545), "eth_start": 1700, "rth_name": "日盤", "eth_name": "夜盤",
        "spread": 8.0, "slippage": 3.0, "commission": 0.0, "swap_long_pct": 3.0, "swap_short_pct": 2.0,
        "usd_per_point": 1.0,
    },
    "HK50": {
        # 恆生指數：期貨日盤 09:15–16:30（午休 12:00–13:00）、夜盤 17:15–翌日 03:00（夜盤歸隔天）
        "name": "恆生指數", "duka": "HKGIDXHKD", "price_range": (8000, 80000),
        "tz": "Asia/Hong_Kong", "day_roll": 1700, "rth": (915, 1630), "eth_start": 1715, "rth_name": "日盤", "eth_name": "夜盤",
        "spread": 8.0, "slippage": 3.0, "commission": 0.0, "swap_long_pct": 6.5, "swap_short_pct": 1.0,
        "usd_per_point": 1.0,
    },
}

DEFAULT = "US500"


def inst(symbol):
    return INSTRUMENTS.get(symbol) or INSTRUMENTS[DEFAULT]


def cost_defaults(symbol):
    i = INSTRUMENTS[symbol]
    return {k: i[k] for k in ("spread", "slippage", "commission", "swap_long_pct", "swap_short_pct", "usd_per_point")}


def hm2min(hhmm):
    return (int(hhmm) // 100) * 60 + int(hhmm) % 100
