"""用真正的後端在「模擬時間」內跑一段交易，錄下每個畫面的 API 回應，給前端展示模式使用。

用法（在 backend 目錄、啟用虛擬環境後）：
    python ../scripts/generate_demo_data.py ../web/src/demo/fixtures.json
    cd ../web && npm run build:demo   # 輸出到 web/dist-demo
"""

import asyncio
import json
import math
import os
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta

OUT = sys.argv[1]
sys.path.insert(0, "/home/user/goldhunter/backend")
os.environ["GOLDHUNTER_DATA_DIR"] = tempfile.mkdtemp()
os.environ["GOLDHUNTER_API_TOKEN"] = "demo"
os.environ["GOLDHUNTER_RESUME_BOTS"] = "false"

random.seed(7)
TICK = timedelta(minutes=15)
N_TICKS = 576
REAL_NOW = datetime.now(UTC).replace(second=0, microsecond=0)
SIM = {"t": REAL_NOW - TICK * N_TICKS}


class SimDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return SIM["t"] if tz else SIM["t"].replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return SIM["t"].replace(tzinfo=None)


from goldhunter.config import get_settings  # noqa: E402

get_settings.cache_clear()
import goldhunter.engine.bot as bot_mod  # noqa: E402
import goldhunter.store.db as db_mod  # noqa: E402

db_mod.datetime = SimDT
bot_mod.datetime = SimDT
import time as _time  # noqa: E402
import types  # noqa: E402

import goldhunter.risk.manager as risk_mod  # noqa: E402

risk_mod.time = types.SimpleNamespace(time=lambda: SIM["t"].timestamp(), gmtime=_time.gmtime, strftime=_time.strftime)

from fastapi.testclient import TestClient  # noqa: E402

from goldhunter.ai.base import AIProvider, AIResult  # noqa: E402
from goldhunter.api import backtest_routes, settings_routes  # noqa: E402
from goldhunter.core.models import Candle  # noqa: E402
from goldhunter.engine import manager as m  # noqa: E402
from goldhunter.exchanges.paper import PaperExchange  # noqa: E402
from goldhunter.intel import sources  # noqa: E402
from goldhunter.intel.hub import IntelSnapshot, hub  # noqa: E402
from goldhunter.main import app  # noqa: E402


# ---------------- 行情：帶趨勢與波動的隨機漫步 ----------------
def walk(start, n, vol, drift_pattern, step_ms=900_000, seed=0):
    rnd = random.Random(seed)
    out, p = [], start
    t0 = int((REAL_NOW - TICK * (n - 60)).timestamp() * 1000)
    for i in range(n):
        drift = drift_pattern(i)
        o = p
        p = p * math.exp(drift + rnd.gauss(0, vol))
        hi = max(o, p) * (1 + abs(rnd.gauss(0, vol / 2)))
        lo = min(o, p) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append(Candle(ts=t0 + i * step_ms, open=o, high=hi, low=lo, close=p, volume=rnd.uniform(50, 400)))
    return out


def regime(i):  # 上漲 → 盤整 → 急跌 → 反彈 → 盤整 → 上漲
    return (0.0004 if i < 380 else 0.0 if i < 480 else -0.0011 if i < 540 else 0.0008 if i < 640 else 0.0 if i < 760 else 0.0004)


SERIES = {
    "BTC": walk(112_400, N_TICKS + 400, 0.0032, regime, seed=1),
    "ETH": walk(4_180, N_TICKS + 400, 0.0045, lambda i: math.sin(i / 25) * 0.0012, seed=2),
    "SOL": walk(208, N_TICKS + 400, 0.006, lambda i: 0.0006, seed=3),
}


class MultiFeed(PaperExchange):
    def __init__(self):
        super().__init__(initial_cash=10_000, fee_rate=0.0005, slippage=0.0002)
        self.i = 300

    async def fetch_candles(self, instrument, timeframe, limit=200):
        s = SERIES[instrument.symbol.split("/")[0]]
        window = s[max(0, self.i - limit): self.i]
        self.set_price(instrument, window[-1].close)
        return window


VETO = [
    "恐懼貪婪指數 81 處於極度貪婪，資金費率 0.031% 偏高，多單擁擠，暫不追多",
    "美國 CPI 將於 6 小時後公布，波動風險高，等數據落地再進場",
    "新聞出現交易所遭駭客攻擊消息，市場情緒轉弱，否決此次開倉",
    "策略近 5 筆 4 敗，且目前為盤整格局，均線交叉假訊號機率高",
]
ADJUST = [
    ("趨勢成立但 ATR 放大 40%，縮小倉位 30%，止損收緊至前低下方", 0.7),
    ("ETF 連續 3 日淨流入、資金費率中性，訊號品質佳，倉位放大 20%", 1.2),
    ("方向正確但即將遇到前高壓力，縮小倉位並設定止盈", 0.6),
]
APPROVE = ["技術面與籌碼面一致，無重大事件，照原訊號執行", "未平倉量同步增加、情緒中性，放行"]
MANAGE_HOLD = ["資金費率正常、無重大新聞，維持持倉", "價格仍在趨勢通道內，維持不動"]


class DemoAI(AIProvider):
    provider = "anthropic"

    def __init__(self, name="claude-opus-5"):
        super().__init__(name)

    async def complete_json(self, system, user, schema):
        r = random.random()
        if "AI 交易員" in system:
            return self._trader(user)
        if "持倉管理員" in system:
            if r < 0.72:
                d = {"action": "hold", "reduce_pct": None, "new_stop": None, "confidence": 0.62,
                     "reasoning": random.choice(MANAGE_HOLD)}
            elif r < 0.9:
                sec = user.split("## 持倉狀況")[-1]
                price = float(sec.split("現價 ")[1].split("，")[0])
                entry = float(sec.split("均價 ")[1].split("，")[0])
                is_long_pos = "方向 long" in sec
                in_profit = (price > entry) if is_long_pos else (price < entry)
                ns = round(entry if in_profit else (price * 0.99 if is_long_pos else price * 1.01), 2)
                d = {"action": "move_stop", "reduce_pct": None, "new_stop": ns, "confidence": 0.7,
                     "reasoning": "已有獲利，止損上移至成本價保本"}
            else:
                d = {"action": "reduce", "reduce_pct": 50, "new_stop": None, "confidence": 0.66,
                     "reasoning": "FOMC 會議紀要即將公布，先減倉一半降低風險"}
        elif "風險分析師" in system:
            # 模擬 AI：讀 prompt 裡的價格與 EMA50，逆勢訊號否決；另有少數依新聞 / 事件否決
            try:
                price = float(user.split("最新價 ")[1].split(chr(10))[0])
                ema50 = float(user.split('"ema50": ')[1].split(",")[0])
            except (IndexError, ValueError):
                price = ema50 = 0
            is_long = '"action": "open_long"' in user
            against = (is_long and price < ema50) or (not is_long and price > ema50)
            if against and r > 0.65:
                d = {"verdict": "adjust", "size_multiplier": 0.5, "stop_loss": None, "take_profit": None,
                     "confidence": 0.6, "reasoning": "逆勢訊號但 RSI 已極度超賣、資金費率轉負，縮小一半倉位試單"}
            elif against:
                d = {"verdict": "veto", "size_multiplier": 1, "stop_loss": None, "take_profit": None,
                     "confidence": 0.8, "reasoning": ("價格位於 EMA50 之下，屬逆勢做多；" if is_long else "價格位於 EMA50 之上，屬逆勢做空；")
                     + "且" + random.choice(["資金費率偏高、多單擁擠", "未平倉量下降、動能不足", "恐懼貪婪指數轉弱"]) + "，否決此次開倉"}
            elif r < 0.12:
                d = {"verdict": "veto", "size_multiplier": 1, "stop_loss": None, "take_profit": None,
                     "confidence": 0.78, "reasoning": random.choice(VETO)}
            elif r < 0.7:
                why, mult = random.choice(ADJUST)
                d = {"verdict": "adjust", "size_multiplier": mult, "stop_loss": None, "take_profit": None,
                     "confidence": 0.72, "reasoning": why}
            else:
                d = {"verdict": "approve", "size_multiplier": 1, "stop_loss": None, "take_profit": None,
                     "confidence": 0.81, "reasoning": random.choice(APPROVE)}
        elif "程式碼審查員" in system:
            if "背離" in user:
                d = {"equivalent": False, "confidence": 0.83, "summary": "背離判斷邏輯未完整轉換",
                     "issues": [{"severity": "high", "description": "Pine 用 ta.pivotlow 找前低做 RSI 背離，Python 只比較最近兩根 K 棒，進場時機會不同"},
                                {"severity": "medium", "description": "strategy.exit 的 trail_points 移動止損沒有轉換"}]}
            else:
                d = {"equivalent": True, "confidence": 0.93, "summary": "進出場條件、ATR 止損與參數預設值皆一致",
                     "issues": [{"severity": "low", "description": "plot() 繪製通道線未轉換，不影響交易"}]}
        elif "策略研究員" in system:
            d = {"params": {"fast": 12, "slow": 34}, "reasoning": "近兩週由單邊上漲轉為區間震盪，縮短均線週期提高反應速度，避免在盤整中頻繁假突破"}
        else:
            d = {"ok": True}
        return AIResult(decision=d, raw_text=json.dumps(d, ensure_ascii=False), model=self.model,
                        input_tokens=random.randint(2800, 4200), output_tokens=random.randint(90, 220))

    def _trader(self, user):
        """模擬 AI 交易員：順勢為主，參考情緒與資金費率；多數時候觀望"""
        sym = user.split("## 商品")[1].strip().split("（")[0].split(":")[1].split("/")[0]
        price = float(user.split("最新價 ")[1].split(chr(10))[0])
        ema50 = float(user.split('"ema50": ')[1].split(",")[0])
        rsi = float(user.split('"rsi14": ')[1].split(",")[0])
        pos = user.split("目前持倉：")[1].split(chr(10))[0]
        r = random.random()
        above = price > ema50 * 1.003
        below = price < ema50 * 0.997
        d = {"action": "hold", "size_pct": 0, "leverage": 1, "stop_loss": None, "take_profit": None,
             "confidence": 0.5, "reasoning": ""}
        if pos.startswith("無"):
            if above and rsi < 68 and r < 0.35:
                d.update(action="open_long", size_pct=random.choice([8, 10, 12]), leverage=2,
                         stop_loss=round(price * 0.982, 2), take_profit=round(price * 1.036, 2), confidence=0.74,
                         reasoning=f"{sym} 站上 EMA50、RSI {rsi:.0f} 未過熱；資金費率中性、ETF 資金持續流入，順勢做多，止損設在前低下方")
            elif below and rsi > 32 and r < 0.3:
                d.update(action="open_short", size_pct=random.choice([6, 8]), leverage=2,
                         stop_loss=round(price * 1.018, 2), take_profit=round(price * 0.964, 2), confidence=0.7,
                         reasoning=f"{sym} 跌破 EMA50、未平倉量增加但價格走弱，空方主導；恐懼貪婪指數由高檔回落，順勢做空")
            else:
                d.update(confidence=0.55, reasoning=random.choice([
                    f"{sym} 在 EMA50 附近整理、方向不明，Core PCE 將於本週公布，先觀望",
                    f"{sym} RSI {rsi:.0f} 位於中性區、成交量萎縮，沒有明確優勢，觀望",
                    f"{sym} 趨勢成立但已連漲多根、追價盈虧比不佳，等回檔再說"]))
        else:
            long = pos.startswith("long")
            if (long and price < ema50) or (not long and price > ema50):
                d.update(action="close", confidence=0.72,
                         reasoning=f"{sym} 價格回到 EMA50 另一側，原本的趨勢假設失效，平倉出場")
            else:
                d.update(confidence=0.6, reasoning=f"{sym} 趨勢仍在、未觸及止損，續抱持倉")
        return AIResult(decision=d, raw_text=json.dumps(d, ensure_ascii=False), model=self.model,
                        input_tokens=random.randint(3600, 5200), output_tokens=random.randint(110, 240))

    async def complete_text(self, system, user, max_tokens=16000):
        return PY_CONVERTED


PINE = """//@version=5
strategy("通道突破", overlay=true, default_qty_type=strategy.percent_of_equity, default_qty_value=10)
length = input.int(20, "通道長度")
atrMult = input.float(2.0, "ATR 倍數")
upper = ta.highest(high, length)
lower = ta.lowest(low, length)
atr = ta.atr(14)
if close > upper[1]
    strategy.entry("L", strategy.long)
    strategy.exit("XL", "L", stop=close - atr * atrMult)
if close < lower[1]
    strategy.close("L")
plot(upper, color=color.green)
plot(lower, color=color.red)"""

PY_CONVERTED = '''```python
# 未轉換項目：
# - plot 繪圖（通道線）
from goldhunter.strategies.sdk import Strategy, ta


class UserStrategy(Strategy):
    name = "channel_breakout"
    description = "通道突破：突破 N 根高點做多，跌破 N 根低點平倉，ATR 止損"
    default_params = {"length": 20, "atr_mult": 2.0, "size_pct": 10}
    warmup = 30

    def on_bar(self, ctx):
        upper = ta.highest(ctx.high, self.p("length"))
        lower = ta.lowest(ctx.low, self.p("length"))
        atr = ta.atr(ctx.high, ctx.low, ctx.close, 14)[-1]
        if upper[-2] is None or lower[-2] is None or atr is None:
            return None
        if ctx.position_size == 0 and ctx.price > upper[-2]:
            return ctx.long(self.p("size_pct"), stop_loss=ctx.price - atr * self.p("atr_mult"),
                            reasoning="收盤突破通道上緣")
        if ctx.position_size > 0 and ctx.price < lower[-2]:
            return ctx.close_position("跌破通道下緣")
        return None
```'''

ai = DemoAI()
feeds = {}


def fake_exchange(acc):
    ex = MultiFeed()
    feeds[len(feeds)] = ex
    return ex


m.exchange_from_account = fake_exchange
m.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")
settings_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")
backtest_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")


async def fetch_history(exchange_id, inst, tf, s, e):
    base = inst.symbol.split("/")[0]
    return walk({"BTC": 58_000, "ETH": 2_400, "SOL": 95}.get(base, 100), 2200, 0.009,
                lambda i: 0.0007 if i < 900 else (-0.0004 if i < 1400 else 0.0009), step_ms=3_600_000, seed=11)


backtest_routes.fetch_history = fetch_history
settings_routes.fetch_history = fetch_history
settings_routes.datetime = SimDT  # 審查通過時間用模擬時鐘，模擬期進度才會正確


async def tv_csv_for(code_md: str) -> str:
    """模擬使用者從 TradingView 匯出的交易清單（時間為台灣時區 UTC+8）"""
    from goldhunter.backtest.engine import BacktestConfig, run_backtest
    from goldhunter.core.models import Instrument
    from goldhunter.risk.manager import RiskConfig
    from goldhunter.strategies.custom import load_strategy_class
    from goldhunter.tradingview.pine_converter import extract_code

    cls = load_strategy_class(extract_code(code_md))
    inst = Instrument.parse("crypto:BTC/USDT:perp")
    candles = await fetch_history("binance", inst, "1h", 0, 0)
    res = await run_backtest(cls(cls.default_params), inst, "1h", candles,
                             BacktestConfig(risk=RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=10_000,
                                                            max_position_pct=100, max_total_exposure_pct=1000,
                                                            require_stop_loss=False)))
    rows, n = ["Trade #,Type,Signal,Date/Time,Price USDT,Contracts,Profit USDT"], 0
    for t in res.trades:
        ts = datetime.fromtimestamp(t.ts / 1000, tz=UTC) + timedelta(hours=8)
        if not t.reduce_only:
            n += 1
            rows.append(f"{n},Entry Long,L,{ts:%Y-%m-%d %H:%M},{t.price:.2f},1,0")
        else:
            rows.append(f"{n},Exit Long,XL,{ts:%Y-%m-%d %H:%M},{t.price:.2f},1,{t.realized_pnl or 0:.2f}")
    return "\n".join(rows)

# 市場情報：恐懼貪婪 / 新聞 / 經濟日曆用即時資料（取不到就用備用），合約數據用示意值
INTEL = {}


async def load_intel():
    fg = await sources.fear_greed()
    news = await sources.news(["BTC", "bitcoin", "crypto"])
    cal = await sources.economic_calendar()
    INTEL.update(fear_greed=fg or {"value": 74, "label": "Greed", "yesterday": 70}, news=news[:10], events=cal[:6])


asyncio.run(load_intel())


async def snap(exchange_id, inst, settings=None):
    base = inst.symbol.split("/")[0]
    der = {"BTC": {"funding_rate_pct": 0.0102, "open_interest": 8_412_000_000, "long_short_ratio": 1.34},
           "ETH": {"funding_rate_pct": 0.0087, "open_interest": 4_960_000_000, "long_short_ratio": 1.12},
           "SOL": {"funding_rate_pct": 0.0154, "open_interest": 1_310_000_000, "long_short_ratio": 1.58}}.get(base, {})
    macro = {
        "CPIAUCSL": {"label": "美國 CPI 指數", "date": "2026-08-01", "value": 327.61, "previous": 326.95, "yoy_pct": 2.84},
        "FEDFUNDS": {"label": "聯邦基金利率 %", "date": "2026-08-01", "value": 3.58, "previous": 3.83},
        "DGS10": {"label": "美國 10 年期公債殖利率 %", "date": "2026-09-25", "value": 4.12, "previous": 4.09},
        "DTWEXBGS": {"label": "美元指數（廣義）", "date": "2026-09-19", "value": 119.84, "previous": 120.31},
        "UNRATE": {"label": "美國失業率 %", "date": "2026-08-01", "value": 4.3, "previous": 4.2},
    }
    return IntelSnapshot(instrument=str(inst), fetched_at=REAL_NOW.strftime("%Y-%m-%d %H:%M"), derivatives=der,
                         macro=macro, fear_greed=INTEL["fear_greed"], news=INTEL["news"],
                         events=[e for e in INTEL["events"]])


hub.snapshot = snap

H = {"Authorization": "Bearer demo"}
fx = {}

with TestClient(app) as c:
    def P(path, body=None):
        r = c.post("/api" + path, headers=H, json=body)
        assert r.status_code < 300, (path, r.text)
        return r.json()

    def G(path):
        r = c.get("/api" + path, headers=H)
        assert r.status_code == 200, (path, r.text)
        return r.json()

    acc1 = P("/accounts", {"name": "Binance 模擬帳戶", "exchange_id": "binance", "paper_cash": 10000})
    acc2 = P("/accounts", {"name": "OKX 模擬帳戶", "exchange_id": "okx", "paper_cash": 10000})
    P("/accounts", {"name": "Bybit 實盤", "exchange_id": "bybit", "paper": False,
                    "api_key": "demo-bybit-key-7Q2k", "secret": "demo-secret"})
    P("/accounts", {"name": "Hyperliquid 模擬", "exchange_id": "hyperliquid", "paper_cash": 5000})
    claude = P("/ai-models", {"name": "Claude 主力", "provider": "anthropic", "model": "claude-opus-5",
                              "api_key": "sk-ant-demo-0000-x9Fa", "options": {"effort": "high"}})
    deepseek = P("/ai-models", {"name": "DeepSeek 省錢", "provider": "deepseek", "model": "deepseek-chat",
                                "api_key": "sk-demo-deepseek-4b1c"})
    st_ma = P("/strategies", {"name": "BTC 均線趨勢", "kind": "ma_cross",
                              "params": {"fast": 9, "slow": 26, "size_pct": 15, "atr_mult": 2.5, "allow_short": True, "leverage": 2}})
    st_rsi = P("/strategies", {"name": "ETH RSI 回歸", "kind": "rsi_reversion",
                               "params": {"length": 14, "oversold": 38, "exit": 55, "size_pct": 12, "stop_pct": 2.5}})
    st_tv = P("/strategies", {"name": "TradingView 訊號", "kind": "tradingview"})
    tv_csv = c.portal.call(tv_csv_for, PY_CONVERTED)
    conv = P("/strategies/convert-pine", {"name": "通道突破（Pine 轉換）", "pine": PINE, "ai_model_id": claude["id"],
                                          "symbol": "crypto:BTC/USDT:perp", "timeframe": "1h", "tv_csv": tv_csv})
    fx["convert_result"] = conv
    fx["convert_failed"] = P("/strategies/convert-pine", {
        "name": "RSI 背離（Pine 轉換）", "ai_model_id": claude["id"],
        "pine": "//@version=5\nstrategy(\"RSI 背離\")\nr = ta.rsi(close, 14)\npl = ta.pivotlow(r, 5, 5)\n// ...背離判斷..."})
    tune_ranges = {"fast": [5, 20], "slow": [20, 60]}
    b0 = P("/bots", {"name": "AI 交易員 · 主力", "account_id": acc1["id"], "ai_model_id": claude["id"],
                     "symbols": ["crypto:BTC/USDT:perp", "crypto:ETH/USDT:perp", "crypto:SOL/USDT:perp"],
                     "timeframe": "1h", "interval_sec": 60, "risk": {"max_leverage": 3, "max_position_pct": 15},
                     "copilot": {"review": False, "manage": False, "tune": False, "event_blackout_min": 60},
                     "ai_trader": {"instructions": "順勢交易為主，不逆勢抄底；重大數據公布前不開新倉；單筆最多 15% 資金、最多 3 倍槓桿。",
                                   "reference_strategy_id": st_ma["id"], "min_confidence": 0.6}})
    b1 = P("/bots", {"name": "BTC 均線 + AI 審核", "account_id": acc1["id"], "strategy_id": st_ma["id"],
                     "ai_model_id": claude["id"], "symbols": ["crypto:BTC/USDT:perp"], "timeframe": "15m",
                     "interval_sec": 60, "risk": {"max_leverage": 3, "max_position_pct": 25},
                     "copilot": {"review": True, "manage": True, "manage_interval_min": 60, "tune": True,
                                 "tune_ranges": tune_ranges, "event_blackout_min": 60,
                                 "notes": "偏好順勢，不要在重大數據公布前追價；盤整時寧可少做"}})
    b3 = P("/bots", {"name": "SOL TradingView 跟單", "account_id": acc1["id"], "strategy_id": st_tv["id"],
                     "symbols": ["crypto:SOL/USDT:perp"], "timeframe": "15m", "interval_sec": 60,
                     "risk": {"max_leverage": 3}, "copilot": {"review": False, "manage": False, "event_blackout_min": 0}})
    P("/bots", {"name": "ETH RSI 回歸 + DeepSeek 審核", "account_id": acc2["id"], "strategy_id": st_rsi["id"],
                "ai_model_id": deepseek["id"], "symbols": ["crypto:ETH/USDT:perp"], "timeframe": "15m",
                "interval_sec": 60, "risk": {"max_leverage": 2},
                "copilot": {"review": True, "manage": False, "tune": False, "event_blackout_min": 30}})
    b2 = b0

    for b in (b1, b2, b3):
        P(f"/bots/{b['id']}/start")
    runners = {b["id"]: m.manager.runners[b["id"]] for b in (b1, b2, b3)}
    for r in runners.values():
        r.interval_sec = 10**7  # 背景迴圈只跑第一次，之後由這支程式手動推進時間
    for r in runners.values():
        r.exchange.i = 300

    tv_script = {40: ("buy", "long"), 95: ("sell", "flat"), 150: ("buy", "long"), 230: ("sell", "flat"),
                 262: ("sell", "short"), 318: ("buy", "flat"), 350: ("buy", "long"), 430: ("sell", "flat"),
                 470: ("buy", "long"), 540: ("sell", "flat"), 560: ("buy", "long")}
    for k in range(N_TICKS):
        SIM["t"] += TICK
        for bid, r in runners.items():
            r.exchange.i += 1
            if bid == b1["id"]:
                r.last_tune = SIM["t"]  # 參數微調稍後手動觸發
            c.portal.call(r.tick)
        if k in tv_script:
            act, mp = tv_script[k]
            c.post(f"/api/tradingview/webhook/{b3['id']}",
                   json={"passphrase": b3["webhook_secret"], "action": act, "market_position": mp, "size_pct": 15,
                         "comment": "SuperTrend 翻多" if mp == "long" else ("SuperTrend 翻空" if mp == "short" else "出場訊號")})
        if k == 300:
            run = c.portal.call(runners[b1["id"]].run_tune)
            P(f"/tuning/{run.id}/apply")
    # 最新一次微調建議（待套用）
    tr = c.portal.call(runners[b1["id"]].run_tune)

    # 回測紀錄
    bt_new = P("/backtests", {"strategy_id": st_ma["id"], "exchange_id": "binance", "symbol": "crypto:BTC/USDT:perp",
                              "timeframe": "1h", "start": "2026-06-01T00:00:00Z", "end": "2026-09-28T00:00:00Z",
                              "initial_cash": 10000})
    P("/backtests", {"strategy_id": conv["strategy"]["id"], "exchange_id": "binance", "symbol": "crypto:ETH/USDT:perp",
                     "timeframe": "1h", "start": "2026-06-01T00:00:00Z", "end": "2026-09-28T00:00:00Z", "initial_cash": 10000})
    P("/backtests", {"strategy_id": st_rsi["id"], "exchange_id": "binance", "symbol": "crypto:ETH/USDT:perp",
                     "timeframe": "1h", "start": "2026-06-01T00:00:00Z", "end": "2026-09-28T00:00:00Z", "initial_cash": 10000})
    bt_main = P("/backtests", {"strategy_id": st_ma["id"], "exchange_id": "binance", "symbol": "crypto:BTC/USDT:perp",
                               "timeframe": "4h", "start": "2026-03-01T00:00:00Z", "end": "2026-09-28T00:00:00Z",
                               "initial_cash": 10000, "params": {"fast": 12, "slow": 34}})

    SIM["t"] = REAL_NOW
    fx["meta"] = G("/meta")
    fx["accounts"] = G("/accounts")
    fx["ai_models"] = G("/ai-models")
    fx["strategies"] = G("/strategies")
    fx["bots"] = G("/bots")
    fx["dashboard"] = G("/dashboard")
    fx["trades"] = G("/trades?limit=2000")
    acted = G("/decisions?limit=1000&hide_hold=true")
    holds = [d for d in G("/decisions?limit=1000") if d["action"] == "hold"][:150]  # 展示資料只保留最近 150 筆觀望
    fx["decisions"] = sorted(acted + holds, key=lambda d: d["ts"], reverse=True)
    fx["tuning"] = G("/tuning")
    fx["equity"] = {str(b["id"]): G(f"/equity?bot_id={b['id']}&hours=720") for b in fx["bots"]}
    fx["positions"] = {str(b["id"]): G(f"/bots/{b['id']}/positions") for b in fx["bots"]}
    fx["backtests"] = G("/backtests")
    fx["backtest_detail"] = {str(x["id"]): G(f"/backtests/{x['id']}") for x in fx["backtests"]}
    fx["backtest_run"] = bt_main
    fx["intel_settings"] = G("/intel/settings")
    fx["intel_snapshot"] = G("/intel/snapshot?symbol=crypto:BTC/USDT:perp&exchange_id=binance")
    fx["generated_at"] = REAL_NOW.isoformat()

with open(OUT, "w") as f:
    json.dump(fx, f, ensure_ascii=False, default=str)
print("bots", [(b["name"], b["equity"], b["baseline_equity"]) for b in fx["bots"]])
print("trades", len(fx["trades"]), "decisions", len(fx["decisions"]), "tuning", len(fx["tuning"]))
from collections import Counter  # noqa: E402

print(Counter((d["source"], d["action"], d["approved"]) for d in fx["decisions"]).most_common(20))
print("size KB", os.path.getsize(OUT) // 1024)
