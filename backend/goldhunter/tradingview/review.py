"""Pine Script 轉換後的自動審查（四關）。

① 安全檢查：AST 白名單（strategies.custom）
② 試跑：用歷史 K 線實際回測，確認能執行、有交易、交易頻率合理
③ AI 交叉檢查：另一次 AI 呼叫逐條比對 Pine 與 Python 的邏輯
④ TradingView 對帳（選用）：上傳 TradingView 策略測試器匯出的交易清單 CSV，逐筆比對進場時間與方向

全部通過 → 自動啟用，但先只能用在模擬帳戶（paper_only），滿 N 天且有足夠模擬成交後才開放實盤。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from goldhunter.ai.base import AIProvider, AIProviderError
from goldhunter.backtest.engine import BacktestConfig, BacktestTrade, run_backtest
from goldhunter.core.models import Candle, Instrument
from goldhunter.risk.manager import RiskConfig
from goldhunter.strategies.custom import StrategyCodeError, load_strategy_class, validate_code

MATCH_THRESHOLD = 0.9  # ④ 進場吻合率門檻


class Stage(BaseModel):
    key: str
    name: str
    status: str  # passed / failed / skipped
    summary: str
    details: list[str] = []


class ReviewReport(BaseModel):
    passed: bool
    stages: list[Stage]
    reviewed_at: str
    symbol: str
    timeframe: str
    metrics: dict = {}


# ---------------------------------------------------------------- ① 安全檢查
def stage_safety(code: str) -> tuple[Stage, type | None]:
    errors = validate_code(code)
    cls = None
    if not errors:
        try:
            cls = load_strategy_class(code)
        except StrategyCodeError as e:
            errors = [str(e)]
        except Exception as e:  # 載入時期錯誤
            errors = [f"{type(e).__name__}: {e}"]
    if errors:
        return Stage(key="safety", name="安全檢查", status="failed", summary="程式碼含有不允許的寫法",
                     details=errors), None
    return Stage(key="safety", name="安全檢查", status="passed", summary="沒有檔案、網路、系統存取等危險寫法"), cls


# ---------------------------------------------------------------- ② 試跑
async def stage_smoke(cls, params: dict, inst: Instrument, timeframe: str, candles: list[Candle]) -> tuple[Stage, dict]:
    try:
        res = await run_backtest(cls(params), inst, timeframe, candles,
                                 BacktestConfig(risk=RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=10_000)))
    except Exception as e:
        return Stage(key="smoke", name="試跑", status="failed", summary="執行時發生錯誤",
                     details=[f"{type(e).__name__}: {e}"]), {}
    m = res.metrics
    details = [f"K 線 {m.get('bars')} 根，成交 {m.get('trades')} 筆，報酬 {m.get('total_return_pct')}%，"
               f"最大回撤 {m.get('max_drawdown_pct')}%"]
    entries = sum(1 for t in res.trades if not t.reduce_only)
    if entries == 0:
        return Stage(key="smoke", name="試跑", status="failed", summary="整段期間完全沒有進場",
                     details=details + ["可能是進場條件轉換錯誤，或這段行情剛好沒有訊號（可換交易對 / 週期重審）"]), m
    if entries > len(candles) * 0.3:
        return Stage(key="smoke", name="試跑", status="failed", summary="進場次數異常頻繁",
                     details=details + [f"{len(candles)} 根 K 線進場 {entries} 次，疑似條件寫錯"]), m
    return Stage(key="smoke", name="試跑", status="passed", summary=f"正常執行，進場 {entries} 次", details=details), m


# ---------------------------------------------------------------- ③ AI 交叉檢查
CROSS_SYSTEM = """你是嚴格的程式碼審查員。比對 TradingView Pine Script 原始碼與轉換後的 Python 策略，
判斷兩者的交易邏輯是否等價：進場條件、出場條件、止損止盈、方向（多/空）、參數預設值、指標計算與取值位置（x[1] 對應 series[-2]）。
忽略繪圖、顏色、標籤等不影響交易的差異。
severity：high＝會導致交易行為不同；medium＝特定情況下可能不同；low＝不影響交易的小差異。
只輸出符合 schema 的 JSON，description 用繁體中文並指出對應的程式碼。"""

CROSS_SCHEMA = {
    "type": "object",
    "properties": {
        "equivalent": {"type": "boolean"},
        "confidence": {"type": "number"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "description": {"type": "string"},
                },
                "required": ["severity", "description"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["equivalent", "confidence", "issues", "summary"],
    "additionalProperties": False,
}


async def stage_cross_check(ai: AIProvider, pine: str, code: str) -> Stage:
    prompt = f"## Pine Script\n```pine\n{pine}\n```\n\n## Python\n```python\n{code}\n```"
    try:
        r = await ai.complete_json(CROSS_SYSTEM, prompt, CROSS_SCHEMA)
    except AIProviderError as e:
        return Stage(key="cross", name="AI 交叉檢查", status="failed", summary="AI 檢查失敗", details=[str(e)])
    d = r.decision
    issues = d.get("issues") or []
    high = [i for i in issues if i.get("severity") == "high"]
    conf = float(d.get("confidence") or 0)
    label = {"high": "嚴重", "medium": "注意", "low": "輕微"}
    details = [f"[{label.get(i.get('severity'), i.get('severity'))}] {i.get('description')}" for i in issues]
    ok = bool(d.get("equivalent")) and not high and conf >= 0.7
    summary = d.get("summary") or ("邏輯一致" if ok else "發現邏輯差異")
    return Stage(key="cross", name="AI 交叉檢查", status="passed" if ok else "failed",
                 summary=f"{summary}（信心 {conf:.0%}）", details=details)


# ---------------------------------------------------------------- ④ TradingView 對帳
class TVEntry(BaseModel):
    ts: datetime  # 以 CSV 內的時間（時區未知）直接解析為 naive
    direction: str  # long / short


_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")


def _parse_dt(text: str) -> datetime | None:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_tv_trades_csv(text: str) -> list[TVEntry]:
    """解析 TradingView 策略測試器「交易清單」匯出的 CSV（英文或中文介面皆可）。只取進場紀錄。"""
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise ValueError("CSV 沒有資料")
    header = [h.strip().lower() for h in rows[0]]

    def col(*keys: str) -> int:
        for i, h in enumerate(header):
            if any(k in h for k in keys):
                return i
        raise ValueError(f"CSV 找不到欄位：{'/'.join(keys)}（請上傳策略測試器的「交易清單」匯出檔）")

    ti = col("type", "類型", "类型")
    di = col("date", "日期", "時間", "时间")
    out: list[TVEntry] = []
    for r in rows[1:]:
        if len(r) <= max(ti, di):
            continue
        typ = r[ti].strip().lower()
        is_entry = "entry" in typ or "進場" in typ or "进场" in typ or "入場" in typ
        if not is_entry:
            continue
        if "long" in typ or "多" in typ:
            direction = "long"
        elif "short" in typ or "空" in typ:
            direction = "short"
        else:
            continue
        ts = _parse_dt(r[di])
        if ts:
            out.append(TVEntry(ts=ts, direction=direction))
    if not out:
        raise ValueError("CSV 裡沒有找到任何進場紀錄")
    out.sort(key=lambda e: e.ts)
    return out


def _our_entries(trades: list[BacktestTrade]) -> list[tuple[datetime, str]]:
    return [(datetime.fromtimestamp(t.ts / 1000, tz=UTC).replace(tzinfo=None), "long" if t.side == "buy" else "short")
            for t in trades if not t.reduce_only]


def compare_entries(tv: list[TVEntry], ours: list[tuple[datetime, str]], bar: timedelta) -> tuple[float, int, list[str]]:
    """逐筆比對進場（容許 ±1 根 K 棒）。TradingView 匯出的時間是圖表時區，自動嘗試 -12h～+14h 找最佳對齊。

    回傳（吻合率, 最佳時差分鐘, 不吻合的範例）
    """
    if not tv or not ours:
        return 0.0, 0, ["沒有可比對的交易"]
    lo, hi = tv[0].ts - timedelta(hours=14) - bar, tv[-1].ts + timedelta(hours=12) + bar
    ours = [o for o in ours if lo <= o[0] <= hi]
    best: tuple[float, int, list[str]] = (0.0, 0, [])
    best_err = float("inf")
    for minutes in range(-12 * 60, 14 * 60 + 1, 30):
        off = timedelta(minutes=minutes)
        shifted = [(e.ts - off, e.direction) for e in tv]
        start, end = shifted[0][0] - bar, shifted[-1][0] + bar
        window = [o for o in ours if start <= o[0] <= end]
        used: set[int] = set()
        matched, misses, err = 0, [], 0.0
        for ts, d in shifted:
            cands = [(abs(ots - ts), i) for i, (ots, od) in enumerate(window)
                     if i not in used and od == d and abs(ots - ts) <= bar]
            hit = min(cands)[1] if cands else None
            if hit is None:
                if len(misses) < 5:
                    misses.append(f"TradingView {ts:%Y-%m-%d %H:%M} UTC {'做多' if d == 'long' else '做空'}：這邊沒有對應進場")
            else:
                used.add(hit)
                matched += 1
                err += abs(window[hit][0] - ts).total_seconds()
        extra = [o for i, o in enumerate(window) if i not in used]
        for ots, od in extra[: max(0, 5 - len(misses))]:
            misses.append(f"這邊 {ots:%Y-%m-%d %H:%M} UTC {'做多' if od == 'long' else '做空'}：TradingView 沒有這筆")
        rate = matched / max(len(shifted), len(window))
        # 吻合率相同時，選時間誤差最小的時差（±1 根 K 棒的容許範圍會讓相鄰時差也「吻合」）
        if rate > best[0] or (rate == best[0] and rate > 0 and err < best_err):
            best, best_err = (rate, minutes, misses), err
    return best


async def stage_tv_match(cls, params: dict, inst: Instrument, timeframe: str, tv: list[TVEntry],
                         candles: list[Candle], bar: timedelta) -> Stage:
    try:
        res = await run_backtest(cls(params), inst, timeframe, candles,
                                 BacktestConfig(risk=RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=10_000,
                                                                max_position_pct=100, max_total_exposure_pct=1000,
                                                                max_leverage=125, require_stop_loss=False)))
    except Exception as e:
        return Stage(key="tv", name="TradingView 對帳", status="failed", summary="回測執行失敗",
                     details=[f"{type(e).__name__}: {e}"])
    rate, off, misses = compare_entries(tv, _our_entries(res.trades), bar)
    tz = f"UTC{'+' if off >= 0 else '-'}{abs(off) // 60}" + (f":{abs(off) % 60:02d}" if off % 60 else "")
    details = [f"TradingView 進場 {len(tv)} 筆；推測 CSV 時區為 {tz}"] + misses
    ok = rate >= MATCH_THRESHOLD
    return Stage(key="tv", name="TradingView 對帳", status="passed" if ok else "failed",
                 summary=f"進場吻合率 {rate:.0%}（門檻 {MATCH_THRESHOLD:.0%}）", details=details)


# ---------------------------------------------------------------- 總流程
def _tf_delta(timeframe: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([mhdw])", timeframe)
    if not m:
        return timedelta(hours=1)
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


async def run_review(*, ai: AIProvider, pine: str, code: str, params: dict, inst: Instrument, timeframe: str,
                     fetch_candles, tv_csv: str | None = None) -> ReviewReport:
    """fetch_candles(start: datetime, end: datetime) -> list[Candle]"""
    stages: list[Stage] = []
    safety, cls = stage_safety(code)
    stages.append(safety)
    metrics: dict = {}
    now = datetime.now(UTC)
    if cls is None:
        stages += [Stage(key=k, name=n, status="skipped", summary="上一關未通過")
                   for k, n in (("smoke", "試跑"), ("cross", "AI 交叉檢查"), ("tv", "TradingView 對帳"))]
    else:
        params = {**cls.default_params, **(params or {})}
        try:
            candles = await fetch_candles(now - timedelta(days=90), now)
            smoke, metrics = await stage_smoke(cls, params, inst, timeframe, candles)
        except Exception as e:
            smoke = Stage(key="smoke", name="試跑", status="failed", summary="下載歷史資料失敗",
                          details=[f"{type(e).__name__}: {e}"])
        stages.append(smoke)
        stages.append(await stage_cross_check(ai, pine, code))
        if tv_csv:
            try:
                tv = parse_tv_trades_csv(tv_csv)
                bar = _tf_delta(timeframe)
                pad = bar * (cls.warmup + 50)
                tv_candles = await fetch_candles((tv[0].ts - timedelta(hours=14)).replace(tzinfo=UTC) - pad,
                                                 (tv[-1].ts + timedelta(hours=14)).replace(tzinfo=UTC) + bar * 5)
                stages.append(await stage_tv_match(cls, params, inst, timeframe, tv, tv_candles, bar))
            except ValueError as e:
                stages.append(Stage(key="tv", name="TradingView 對帳", status="failed", summary="CSV 無法解析",
                                    details=[str(e)]))
            except Exception as e:
                stages.append(Stage(key="tv", name="TradingView 對帳", status="failed", summary="對帳失敗",
                                    details=[f"{type(e).__name__}: {e}"]))
        else:
            stages.append(Stage(key="tv", name="TradingView 對帳", status="skipped",
                                summary="未上傳交易清單 CSV（上傳後可確認與 TradingView 結果一致）"))
    passed = all(s.status == "passed" for s in stages if s.key != "tv") and all(
        s.status != "failed" for s in stages)
    return ReviewReport(passed=passed, stages=stages, reviewed_at=now.isoformat(), symbol=str(inst),
                        timeframe=timeframe, metrics=metrics)
