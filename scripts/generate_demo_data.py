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
    "XRP": walk(2.84, N_TICKS + 400, 0.0055, lambda i: -0.0003 if i < 600 else 0.0007, seed=4),
}


class MultiFeed(PaperExchange):
    def __init__(self, cash=10_000):
        super().__init__(initial_cash=cash, fee_rate=0.0005, slippage=0.0002)
        self.i = 300

    async def perp_volumes(self):
        return [("BTC", 3.1e10), ("ETH", 1.6e10), ("DOGE", 4.2e9), ("SOL", 3.8e9), ("PEPE", 1.2e9), ("XRP", 1.1e9)]

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
        if "量化組合設計師" in system:
            name = user.split("請替 ")[1].split(" 設計")[0]
            return self._res(MASTER_BY_NAME[name]["plan"])
        if "AI 交易員" in system:
            r = self._trader(user)
            if "你的交易大腦：" in system:
                name = system.split("你的交易大腦：")[1].split(chr(10))[0]
                d = json.loads(r.raw_text)
                go, wait = STYLE.get(name, ("趨勢成立", "沒有明確優勢"))
                if d["action"] in ("open_long", "open_short"):
                    d["reasoning"] = f"以{name}的角度：{go}。" + d["reasoning"]
                elif d["action"] == "hold" and "觀望" in d["reasoning"]:
                    d["reasoning"] = f"以{name}的角度：{wait}。"
                r = AIResult(decision=d, raw_text=json.dumps(d, ensure_ascii=False), model=r.model,
                             input_tokens=r.input_tokens + 6000, output_tokens=r.output_tokens)
            return r
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

    def _res(self, d):
        return AIResult(decision=d, raw_text=json.dumps(d, ensure_ascii=False), model=self.model,
                        input_tokens=random.randint(2000, 4000), output_tokens=random.randint(100, 400))

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


# ---------------- 投資大師：模擬使用者用女媧蒸餾好、上傳的檔案 ----------------
def nuwa_skill(slug, name, quote, identity, models, rules, values, bounds):
    """產生女媧格式的 SKILL.md（含對話用段落，上傳後會被捨棄）"""
    ms = "\n".join(f"### 模型{i + 1}：{t}\n一句話：{d}\n" for i, (t, d) in enumerate(models))
    rs = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))
    return f"""---
name: {slug}-perspective
description: |
  {name}的思維操作系統。依公開著作、訪談與傳記提煉，聚焦交易與風險管理。
---

# {name} · 思維操作系統

> {quote}

## 角色扮演規則
用第一人稱、以{name}的口吻回答；不要跳出角色。

## 回答工作流（Agentic Protocol）
遇到需要事實的問題，先上網搜尋最新資料再回答。

## 身份卡
{identity}

## 核心心智模型
{ms}
## 決策啟發式
{rs}

## 價值觀與反模式
{chr(10).join('- ' + v for v in values)}

## 誠實邊界
{chr(10).join('- ' + b for b in bounds)}
- 本檔案是依公開資料推論的思維框架，不代表本人觀點

## 調研來源
- 公開著作、訪談、傳記（略）
"""


def nuwa_fidelity(name, score, grade, dims, judge):
    rows = "\n".join(f"| {n} | {s}/{mx} | {r} |" for n, s, mx, r in dims)
    return f"""# {name} · 保真度評分卡

測試日期：2026-09-20

**總分：{score}/100 · 等級 {grade}**

| 維度 | 得分 | 說明 |
|---|---|---|
{rows}

> {judge}
"""


MASTERS = [
    dict(slug="livermore", name="傑西·李佛摩", file="livermore-perspective.zip", score=86, grade="A",
         quote="「賺大錢靠的是坐著等，不是頻繁交易。」",
         identity="二十世紀初的投機之王，專做趨勢與關鍵點突破；曾多次破產又東山再起。",
         models=[("最小阻力線", "價格沿阻力最小的方向走，只順著它交易"),
                 ("關鍵點", "只在價格突破關鍵點時進場，其他時間等待"),
                 ("試單加碼", "先小部位試單，對了才加碼，錯了立刻出場"),
                 ("市場永遠是對的", "不和行情爭辯，錯了就認錯")],
         rules=["突破前高且放量才做多，跌破前低才做空", "單筆虧損不超過資金 10%", "絕不攤平虧損部位",
                "盤整區間中間不交易", "獲利部位讓它跑，用移動止損保護"],
         values=["攤平是大忌", "聽消息交易是自殺", "過度交易是虧損的根源"],
         bounds=["不懂現代衍生品與加密貨幣的結構", "盤整行情中表現差"],
         dims=[("立場一致性", 27, 30, "不攤平、等待、固定止損三題都一致"), ("風格辨識度", 16, 20, "關鍵點、最小阻力線等用語明顯"),
               ("邊緣誠實度", 18, 20, "超範圍題有標註推斷"), ("情境合理性", 13, 15, "突破試單、止損設在整理區內"),
               ("結構完整度", 12, 15, "心智模型與決策規則完整")],
         judge="立場與風格都很像李佛摩；超範圍題有標註推斷。",
         plan={"suitable": True, "style_summary": "順勢突破，集中持有最強的 2 個幣",
               "reason": "李佛摩只在關鍵點突破時進場、錯了立刻認錯，並且集中火力在領頭羊；所以用 4 小時線抓波段、只挑成交量最大的幣、最多持有 2 個標的，並用掛單等待回測突破點來改善盈虧比。",
               "timeframe": "4h", "holding_period": "數天到數週",
               "universe": {"mode": "rules", "top_n": 5, "exclude_meme": True, "include_only": [], "symbols": []},
               "max_positions": 2, "position_pct": 20, "max_leverage": 3, "allow_short": True, "entry_mode": "smart",
               "instructions": "只在突破前高（做多）或跌破前低（做空）時進場，盤整不做；止損設在關鍵點另一側，單筆虧損不超過 10%；絕不攤平，獲利後才加碼。"},
         capital=10_000),
    dict(slug="soros", name="喬治·索羅斯", file="soros-perspective.zip", score=78, grade="B",
         quote="「重要的不是你對或錯，而是你對的時候賺多少、錯的時候賠多少。」",
         identity="量子基金創辦人，以反身性理論與總經押注聞名，1992 年放空英鎊。",
         models=[("反身性", "市場參與者的認知會改變基本面，形成自我強化的循環"),
                 ("易錯性", "所有人的認知都有缺陷，包括自己"),
                 ("泡沫階段", "辨識繁榮—蕭條循環走到哪個階段"),
                 ("先投資再調查", "先建小部位，再邊觀察邊調整")],
         rules=["看流動性與政策方向決定大方向", "在泡沫早期順勢做多，確認反轉時大舉做空", "發現自己錯了立刻反向",
                "有把握時重倉", "只做流動性最好的市場"],
         values=["堅持錯誤的觀點比犯錯更糟", "不做看不懂流動性的市場"],
         bounds=["總經判斷需要時間驗證，短線訊號不準", "加密貨幣的反身性判斷屬框架推斷"],
         dims=[("立場一致性", 24, 30, "反身性、易錯性立場正確"), ("風格辨識度", 15, 20, "有「我可能是錯的」的語氣"),
               ("邊緣誠實度", 17, 20, "有標註推斷"), ("情境合理性", 10, 15, "應更強調流動性與政策面"),
               ("結構完整度", 12, 15, "結構完整")],
         judge="反身性框架掌握到位，交易情境題略偏短線。",
         plan={"suitable": True, "style_summary": "總經驅動，重倉 BTC、ETH 雙向操作",
               "reason": "索羅斯看流動性與政策面決定方向，只做流動性最好的市場，有把握時重倉、錯了立刻反向；所以用日線、只做 BTC 與 ETH，倉位較大、允許做空。",
               "timeframe": "1d", "holding_period": "數週到數月",
               "universe": {"mode": "list", "top_n": 10, "exclude_meme": True, "include_only": [], "symbols": ["BTC", "ETH"]},
               "max_positions": 2, "position_pct": 30, "max_leverage": 2, "allow_short": True, "entry_mode": "market",
               "instructions": "先判斷美元流動性與利率方向，再決定多空；在泡沫早期順勢、確認反轉時反手；看錯立刻平倉並反向；重大數據前降低部位。"},
         capital=10_000),
    dict(slug="ptj", name="保羅·都鐸·瓊斯", file="paul-tudor-jones-perspective.zip", score=81, grade="B",
         quote="「最重要的原則是防守，而不是進攻。」",
         identity="都鐸投資創辦人，1987 年股災前放空獲利；以嚴格風控與 200 日均線紀律著稱。",
         models=[("防守第一", "先想會賠多少，再想會賺多少"), ("200 日均線", "價格在 200 日均線下方就不做多"),
                 ("5:1 盈虧比", "只做潛在報酬是風險 5 倍的交易"), ("虧損時縮手", "連續虧損就降低部位")],
         rules=["價格在長期均線下方不做多", "盈虧比不到 3 倍不進場", "連虧兩筆部位減半", "永遠設止損", "分散在幾個相關性低的標的"],
         values=["不攤平", "不當英雄，不接落下的刀"],
         bounds=["不擅長加密貨幣的極端波動", "偏好流動性高的市場"],
         dims=[("立場一致性", 25, 30, "防守第一、均線紀律一致"), ("風格辨識度", 15, 20, "語氣務實"),
               ("邊緣誠實度", 17, 20, "有標註推斷"), ("情境合理性", 12, 15, "盈虧比要求明確"), ("結構完整度", 12, 15, "完整")],
         judge="風控紀律與盈虧比要求都很到位。",
         plan={"suitable": True, "style_summary": "防守型趨勢組合，只做高盈虧比的機會",
               "reason": "瓊斯強調防守第一、只做高盈虧比交易、價格在長期均線下方不做多；所以分散在 3 個主流幣、低槓桿、單一標的倉位小。",
               "timeframe": "4h", "holding_period": "數天",
               "universe": {"mode": "rules", "top_n": 4, "exclude_meme": True, "include_only": [], "symbols": []},
               "max_positions": 3, "position_pct": 12, "max_leverage": 2, "allow_short": True, "entry_mode": "market",
               "instructions": "價格在 EMA200 下方不做多；盈虧比不到 3 倍不進場；每筆必設止損；連虧兩筆後部位減半。"},
         capital=10_000),
    dict(slug="crypto-scalper", name="示範加密短線交易員", file="crypto-scalper-perspective.zip", score=74, grade="B",
         quote="「流動性在哪裡，價格就往哪裡去。」",
         identity="虛構的示範人物：依公開貼文與鏈上成交紀錄風格合成，專做主流幣短線。",
         models=[("掃流動性", "價格先掃掉前高前低的止損單，再反向"), ("資金費率", "費率過高代表多單擁擠"),
                 ("快進快出", "持倉以小時計，不過夜抱單")],
         rules=["掃完前低收回才做多", "資金費率過高不追多", "止損固定 1R，目標 2R", "一天最多虧 3R 就停手"],
         values=["不抱虧損單", "不追已經走完的行情"],
         bounds=["行為依鏈上紀錄推論", "單邊大趨勢中表現差"],
         dims=[("立場一致性", 22, 30, "依鏈上紀錄推論的習慣一致"), ("風格辨識度", 15, 20, "短線語氣明顯"),
               ("邊緣誠實度", 15, 20, "有標註推斷"), ("情境合理性", 11, 15, "與持倉時間相符"), ("結構完整度", 11, 15, "完整")],
         judge="短線風格辨識度高，但樣本期間較短。",
         plan={"suitable": True, "style_summary": "主流幣短線，快進快出、多標的分散",
               "reason": "這位交易員持倉以小時計、固定 1R 止損 2R 目標，偏好在掃完流動性後進場；所以用 1 小時線、分散 4 個標的、掛單等待回檔進場。",
               "timeframe": "1h", "holding_period": "數小時到一天",
               "universe": {"mode": "rules", "top_n": 6, "exclude_meme": True, "include_only": [], "symbols": []},
               "max_positions": 4, "position_pct": 8, "max_leverage": 5, "allow_short": True, "entry_mode": "smart",
               "instructions": "等價格掃過前高前低後收回再進場；止損 1R、目標 2R；資金費率過高不追多；單日虧損 3R 停手。"},
         capital=5_000),
    dict(slug="buffett", name="華倫·巴菲特", file="buffett-perspective.zip", score=88, grade="A",
         quote="「第一條規則：不要賠錢。第二條規則：不要忘記第一條。」",
         identity="波克夏·海瑟威董事長，價值投資代表人物，長期持有優質企業。",
         models=[("能力圈", "只投資自己看得懂的東西"), ("安全邊際", "價格要明顯低於內在價值"), ("長期持有", "理想的持有期是永遠")],
         rules=["不用槓桿", "看不懂的不碰", "別人貪婪時恐懼"],
         values=["反對投機與槓桿", "加密貨幣不產生現金流"],
         bounds=["對加密貨幣持否定態度", "不做短線與放空"],
         dims=[("立場一致性", 28, 30, "反對槓桿立場一致"), ("風格辨識度", 18, 20, "語氣鮮明"),
               ("邊緣誠實度", 19, 20, "明說不在能力圈"), ("情境合理性", 11, 15, "情境題選擇不交易"), ("結構完整度", 12, 15, "完整")],
         judge="價值投資框架非常到位，也誠實表明加密貨幣不在能力圈。",
         plan={"suitable": False, "style_summary": "最保守版本：只做多 BTC、1 倍、日線",
               "reason": "巴菲特明確反對槓桿與投機，並認為加密貨幣不產生現金流、不在他的能力圈；這裡只給最保守的版本（1 倍、只做多、只做 BTC、低倉位），建議等美股 / 台股上線後再用他的思維。",
               "timeframe": "1d", "holding_period": "數月以上",
               "universe": {"mode": "list", "top_n": 10, "exclude_meme": True, "include_only": [], "symbols": ["BTC"]},
               "max_positions": 1, "position_pct": 10, "max_leverage": 1, "allow_short": False, "entry_mode": "smart",
               "instructions": "只在市場極度恐懼、價格明顯低於長期均線時小量做多，不用槓桿、不做空，長期持有。"},
         capital=None),
]
MASTER_BY_NAME = {x["name"]: x for x in MASTERS}
STYLE = {
    "傑西·李佛摩": ("價格突破關鍵點、沿最小阻力線", "還在整理區中間，等關鍵點突破再動手"),
    "喬治·索羅斯": ("美元流動性轉鬆、市場認知正在自我強化", "流動性方向不明，先不下注"),
    "保羅·都鐸·瓊斯": ("價格在長期均線上方、盈虧比超過 3 倍", "盈虧比不到 3 倍，防守第一，不做"),
    "示範加密短線交易員": ("掃完前低的止損後收回", "資金費率偏高、沒有掃流動性的訊號，不追"),
}


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


def fake_exchange(acc, capital=None):
    ex = MultiFeed(capital or acc.paper_cash or 10_000)
    feeds[len(feeds)] = ex
    return ex


m.exchange_from_account = fake_exchange
m.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")
settings_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")
backtest_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")


async def fetch_history(exchange_id, inst, tf, s, e):
    base = inst.symbol.split("/")[0]
    cs = walk(100, 2200, 0.009, lambda i: 0.0007 if i < 900 else (-0.0004 if i < 1400 else 0.0009),
              step_ms=3_600_000, seed=11)
    k = SERIES.get(base, SERIES["BTC"])[N_TICKS + 299].close / cs[-1].close  # 最後價格對齊監控頁的行情
    return [Candle(ts=c.ts, open=c.open * k, high=c.high * k, low=c.low * k, close=c.close * k, volume=c.volume)
            for c in cs]


backtest_routes.fetch_history = fetch_history
settings_routes.fetch_history = fetch_history
settings_routes.datetime = SimDT  # 審查通過時間用模擬時鐘，模擬期進度才會正確
from goldhunter.api import persona_routes  # noqa: E402

persona_routes.datetime = SimDT
persona_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")
from goldhunter.api import analysis_routes  # noqa: E402

analysis_routes.fetch_history = fetch_history
analysis_routes.provider_from_config = lambda cfg: DemoAI(cfg.model or "claude-opus-5")


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
    P("/strategies", {"name": "MRSPENCER v4.6B 黃金", "kind": "mrspencer"})
    tv_csv = c.portal.call(tv_csv_for, PY_CONVERTED)
    conv = P("/strategies/convert-pine", {"name": "通道突破（Pine 轉換）", "pine": PINE, "ai_model_id": claude["id"],
                                          "symbol": "crypto:BTC/USDT:perp", "timeframe": "1h", "tv_csv": tv_csv})
    fx["convert_result"] = conv
    fx["convert_failed"] = P("/strategies/convert-pine", {
        "name": "RSI 背離（Pine 轉換）", "ai_model_id": claude["id"],
        "pine": "//@version=5\nstrategy(\"RSI 背離\")\nr = ta.rsi(close, 14)\npl = ta.pivotlow(r, 5, 5)\n// ...背離判斷..."})
    tune_ranges = {"fast": [5, 20], "slow": [20, 60]}
    import base64
    import io
    import zipfile

    def zipped(mst):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            d = mst["file"].removesuffix(".zip")
            z.writestr(f"{d}/SKILL.md", nuwa_skill(mst["slug"], mst["name"], mst["quote"], mst["identity"],
                                                   mst["models"], mst["rules"], mst["values"], mst["bounds"]))
            z.writestr(f"{d}/FIDELITY.md", nuwa_fidelity(mst["name"], mst["score"], mst["grade"], mst["dims"],
                                                         mst["judge"]))
            z.writestr(f"{d}/references/research.md", "## 調研\n公開資料摘要（略）")
        return base64.b64encode(buf.getvalue()).decode()

    master_bots = []
    for mst in MASTERS:
        pm = P("/personas/upload", {"filename": mst["file"], "content_base64": zipped(mst)})
        plan = P(f"/personas/{pm['id']}/portfolio-plan", {"ai_model_id": claude["id"]})
        if mst["capital"]:
            r = P(f"/personas/{pm['id']}/portfolio", {"ai_model_id": claude["id"], "account_id": acc1["id"],
                                                      "plan": plan, "capital": mst["capital"], "start": False})
            master_bots.append(r["bot_id"])
    # 未達標示範：沒有附評分卡的上傳
    low = MASTERS[3]
    P("/personas/upload", {"filename": "SKILL.md", "content_base64": base64.b64encode(nuwa_skill(
        "my-trader", "我的交易筆記", "「只做看得懂的行情。」", "使用者自己整理的交易習慣。", low["models"], low["rules"],
        low["values"], low["bounds"]).encode()).decode()})
    b0 = P("/bots", {"name": "AI 交易員 · 主流幣", "account_id": acc1["id"], "ai_model_id": claude["id"],
                     "symbols": [], "universe": {"mode": "rules", "top_n": 3, "exclude_meme": True},
                     "timeframe": "1h", "interval_sec": 60, "risk": {"max_leverage": 3, "max_position_pct": 15},
                     "entry": {"mode": "smart", "max_wait_bars": 4, "skip_negative_ev": True},
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
    for bid in master_bots:
        P(f"/bots/{bid}/start")
    runners = {bid: m.manager.runners[bid] for bid in [b1["id"], b2["id"], b3["id"], *master_bots]}
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
    fx["personas"] = G("/personas")
    fx["persona_detail"] = {str(p["id"]): G(f"/personas/{p['id']}") for p in fx["personas"]}
    fx["masters"] = G("/masters")
    fx["entry_analysis"] = {str(bid): G(f"/bots/{bid}/entry-analysis") for bid in runners}
    fx["analysis_entry"] = P("/analysis/entry", {"strategy_id": st_ma["id"], "symbol": "crypto:BTC/USDT:perp",
                                                 "timeframe": "1h"})
    fx["analysis_entry_long"] = P("/analysis/entry", {"strategy_id": st_ma["id"], "symbol": "crypto:BTC/USDT:perp",
                                                      "timeframe": "1h", "direction": "long"})
    fx["analysis_entry_short"] = P("/analysis/entry", {"strategy_id": st_ma["id"], "symbol": "crypto:BTC/USDT:perp",
                                                       "timeframe": "1h", "direction": "short"})
    fx["intel_settings"] = G("/intel/settings")
    fx["intel_snapshot"] = G("/intel/snapshot?symbol=crypto:BTC/USDT:perp&exchange_id=binance")
    fx["generated_at"] = REAL_NOW.isoformat()

with open(OUT, "w") as f:
    json.dump(fx, f, ensure_ascii=False, default=str)
print("bots", [(b["name"], b["equity"], b["baseline_equity"]) for b in fx["bots"]])
print("trades", len(fx["trades"]), "decisions", len(fx["decisions"]), "tuning", len(fx["tuning"]))
from collections import Counter  # noqa: E402

print(Counter((d["source"], d["action"], d["approved"]) for d in fx["decisions"]).most_common(20))
print("masters", [(x["name"], x["return_pct"], x["trades"]) for x in fx["masters"]])
print("size KB", os.path.getsize(OUT) // 1024)
