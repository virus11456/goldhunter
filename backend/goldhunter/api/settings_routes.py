"""設定介面：交易所帳戶、AI 模型、策略（含 Pine Script 轉換）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from goldhunter.ai.base import AIProviderError
from goldhunter.ai.registry import available_providers
from goldhunter.backtest.data import fetch_history
from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.models import Instrument
from goldhunter.core.secrets import decrypt, encrypt, mask
from goldhunter.engine.manager import exchange_from_account, provider_from_config
from goldhunter.exchanges.registry import available_exchanges
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import AIModelConfig, Bot, ExchangeAccount, StrategyConfig, get_session
from goldhunter.strategies.custom import TEMPLATE, StrategyCodeError, load_strategy_class, validate_code
from goldhunter.strategies.lifecycle import STATUS_LABEL, maybe_promote, promotion_progress
from goldhunter.strategies.registry import BUILTIN, list_strategy_types
from goldhunter.tradingview.pine_converter import convert_pine
from goldhunter.tradingview.review import run_review

router = APIRouter()

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


@router.get("/meta")
def meta():
    return {
        "exchanges": available_exchanges(),
        "ai_providers": available_providers(),
        "strategy_types": list_strategy_types(),
        "timeframes": TIMEFRAMES,
        "risk_defaults": RiskConfig().model_dump(),
        "copilot_defaults": CopilotConfig().model_dump(),
        "strategy_template": TEMPLATE,
    }


# ------------------------------ 交易所帳戶 ------------------------------
class AccountIn(BaseModel):
    name: str
    exchange_id: str
    api_key: str | None = None  # 留空＝不修改
    secret: str | None = None
    passphrase: str | None = None
    testnet: bool = False
    paper: bool = True
    paper_cash: float = 10_000.0


def account_out(a: ExchangeAccount) -> dict:
    return {
        "id": a.id, "name": a.name, "exchange_id": a.exchange_id, "testnet": a.testnet, "paper": a.paper,
        "paper_cash": a.paper_cash, "created_at": a.created_at,
        "api_key": mask(decrypt(a.api_key_enc)), "has_secret": bool(a.secret_enc),
        "has_passphrase": bool(a.passphrase_enc),
    }


@router.get("/accounts")
def list_accounts(s: Session = Depends(get_session)):
    return [account_out(a) for a in s.exec(select(ExchangeAccount))]


@router.post("/accounts")
def create_account(body: AccountIn, s: Session = Depends(get_session)):
    if body.exchange_id not in {e["id"] for e in available_exchanges() if not e.get("planned")}:
        raise HTTPException(400, "尚未支援此交易所")
    if not body.paper and not (body.api_key and body.secret):
        raise HTTPException(400, "實盤帳戶需要 API Key 與 Secret")
    a = ExchangeAccount(
        name=body.name, exchange_id=body.exchange_id, testnet=body.testnet, paper=body.paper,
        paper_cash=body.paper_cash, api_key_enc=encrypt(body.api_key), secret_enc=encrypt(body.secret),
        passphrase_enc=encrypt(body.passphrase),
    )
    s.add(a)
    s.commit()
    s.refresh(a)
    return account_out(a)


@router.put("/accounts/{acc_id}")
def update_account(acc_id: int, body: AccountIn, s: Session = Depends(get_session)):
    a = s.get(ExchangeAccount, acc_id) or _404()
    if body.exchange_id not in {e["id"] for e in available_exchanges() if not e.get("planned")}:
        raise HTTPException(400, "尚未支援此交易所")
    if not body.paper and not ((body.api_key or a.api_key_enc) and (body.secret or a.secret_enc)):
        raise HTTPException(400, "實盤帳戶需要 API Key 與 Secret")
    a.name, a.exchange_id, a.testnet, a.paper, a.paper_cash = (
        body.name, body.exchange_id, body.testnet, body.paper, body.paper_cash)
    if body.api_key:
        a.api_key_enc = encrypt(body.api_key)
    if body.secret:
        a.secret_enc = encrypt(body.secret)
    if body.passphrase:
        a.passphrase_enc = encrypt(body.passphrase)
    s.add(a)
    s.commit()
    s.refresh(a)
    return account_out(a)


@router.delete("/accounts/{acc_id}")
def delete_account(acc_id: int, s: Session = Depends(get_session)):
    a = s.get(ExchangeAccount, acc_id) or _404()
    if s.exec(select(Bot).where(Bot.account_id == acc_id)).first():
        raise HTTPException(400, "仍有 Bot 使用此帳戶")
    s.delete(a)
    s.commit()
    return {"ok": True}


@router.post("/accounts/{acc_id}/test")
async def test_account(acc_id: int, s: Session = Depends(get_session)):
    a = s.get(ExchangeAccount, acc_id) or _404()
    ex = None
    try:
        ex = exchange_from_account(a)
        bal = await ex.fetch_balance()
        return {"ok": True, "balance": bal.model_dump()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if ex:
            await ex.close()


# ------------------------------ AI 模型 ------------------------------
class AIModelIn(BaseModel):
    name: str
    provider: str
    model: str = ""
    api_key: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = {}


def ai_out(m: AIModelConfig) -> dict:
    return {"id": m.id, "name": m.name, "provider": m.provider, "model": m.model, "base_url": m.base_url,
            "options": m.options, "api_key": mask(decrypt(m.api_key_enc)), "created_at": m.created_at}


@router.get("/ai-models")
def list_ai(s: Session = Depends(get_session)):
    return [ai_out(m) for m in s.exec(select(AIModelConfig))]


@router.post("/ai-models")
def create_ai(body: AIModelIn, s: Session = Depends(get_session)):
    if body.provider not in {p["id"] for p in available_providers()}:
        raise HTTPException(400, "未知的 AI 供應商")
    m = AIModelConfig(name=body.name, provider=body.provider, model=body.model, base_url=body.base_url or None,
                      options=body.options, api_key_enc=encrypt(body.api_key))
    s.add(m)
    s.commit()
    s.refresh(m)
    return ai_out(m)


@router.put("/ai-models/{mid}")
def update_ai(mid: int, body: AIModelIn, s: Session = Depends(get_session)):
    m = s.get(AIModelConfig, mid) or _404()
    m.name, m.provider, m.model, m.base_url, m.options = (
        body.name, body.provider, body.model, body.base_url or None, body.options)
    if body.api_key:
        m.api_key_enc = encrypt(body.api_key)
    s.add(m)
    s.commit()
    s.refresh(m)
    return ai_out(m)


@router.delete("/ai-models/{mid}")
def delete_ai(mid: int, s: Session = Depends(get_session)):
    m = s.get(AIModelConfig, mid) or _404()
    if s.exec(select(Bot).where(Bot.ai_model_id == mid)).first():
        raise HTTPException(400, "仍有 Bot 使用此模型")
    s.delete(m)
    s.commit()
    return {"ok": True}


@router.post("/ai-models/{mid}/test")
async def test_ai(mid: int, s: Session = Depends(get_session)):
    m = s.get(AIModelConfig, mid) or _404()
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    try:
        r = await provider_from_config(m).complete_json("你是連線測試。", '回傳 {"ok": true}', schema)
        return {"ok": True, "model": r.model, "reply": r.decision}
    except AIProviderError as e:
        return {"ok": False, "error": str(e)}


# ------------------------------ 策略 ------------------------------
class StrategyIn(BaseModel):
    name: str
    kind: str
    params: dict[str, Any] = {}
    code: str | None = None


def strategy_out(st: StrategyConfig, s: Session | None = None) -> dict:
    d = st.model_dump()
    d["status_label"] = STATUS_LABEL.get(st.status, st.status)
    d["paper_progress"] = promotion_progress(st, s) if s is not None else None
    return d


def _check_strategy(body: StrategyIn) -> None:
    if body.kind == "python":
        try:
            load_strategy_class(body.code or "")
        except StrategyCodeError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(400, f"程式載入失敗：{type(e).__name__}: {e}") from e
    elif body.kind not in BUILTIN and body.kind != "tradingview":
        raise HTTPException(400, "未知的策略類型")


@router.get("/strategies")
def list_strategies(s: Session = Depends(get_session)):
    out = []
    for x in s.exec(select(StrategyConfig)).all():
        maybe_promote(x, s)  # 模擬期滿自動開放實盤
        out.append(strategy_out(x, s))
    return out


@router.post("/strategies")
def create_strategy(body: StrategyIn, s: Session = Depends(get_session)):
    _check_strategy(body)
    st = StrategyConfig(name=body.name, kind=body.kind, params=body.params, code=body.code)
    if body.kind in BUILTIN and BUILTIN[body.kind].paper_first:
        # 由 Pine 轉換而來的內建策略：和貼上的 Pine 策略一樣，先跑模擬期才能上實盤
        st.status, st.approved_at = "paper_only", datetime.now(UTC)
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st, s)


@router.put("/strategies/{sid}")
def update_strategy(sid: int, body: StrategyIn, s: Session = Depends(get_session)):
    st = s.get(StrategyConfig, sid) or _404()
    _check_strategy(body)
    if body.code != st.code and body.kind == "python":
        st.status, st.review, st.approved_at = "pending_review", {}, None  # 程式碼有改就要重新審核
    st.name, st.kind, st.params, st.code = body.name, body.kind, body.params, body.code
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st, s)


@router.delete("/strategies/{sid}")
def delete_strategy(sid: int, s: Session = Depends(get_session)):
    st = s.get(StrategyConfig, sid) or _404()
    if s.exec(select(Bot).where(Bot.strategy_id == sid)).first():
        raise HTTPException(400, "仍有 Bot 使用此策略")
    s.delete(st)
    s.commit()
    return {"ok": True}


@router.post("/strategies/{sid}/activate")
def activate_strategy(sid: int, s: Session = Depends(get_session)):
    """使用者已檢視程式碼，確認啟用"""
    st = s.get(StrategyConfig, sid) or _404()
    if st.kind == "python":
        errors = validate_code(st.code or "")
        if errors:
            raise HTTPException(400, "；".join(errors))
    st.status = "active"
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st, s)


class CodeIn(BaseModel):
    code: str


@router.post("/strategies/validate")
def validate_strategy(body: CodeIn):
    errors = validate_code(body.code)
    if not errors:
        try:
            cls = load_strategy_class(body.code)
            return {"ok": True, "errors": [], "name": cls.name, "default_params": cls.default_params,
                    "uses_ai": cls.uses_ai}
        except Exception as e:
            errors = [f"{type(e).__name__}: {e}"]
    return {"ok": False, "errors": errors}


class ReviewSettings(BaseModel):
    """自動審查用的行情：交易所、交易對、K 線週期，以及選填的 TradingView 交易清單 CSV"""

    exchange_id: str = "binance"
    symbol: str = "crypto:BTC/USDT:perp"
    timeframe: str = "1h"
    tv_csv: str | None = None


class PineIn(ReviewSettings):
    name: str
    pine: str
    ai_model_id: int


class ReviewIn(ReviewSettings):
    ai_model_id: int


async def _review_and_save(st: StrategyConfig, ai, body: ReviewSettings, s: Session) -> dict:
    """跑四關審查；通過 → 進入模擬期（paper_only），否則維持待審核"""
    try:
        inst = Instrument.parse(body.symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    async def candles(start, end):
        return await fetch_history(body.exchange_id, inst, body.timeframe, int(start.timestamp() * 1000),
                                   int(end.timestamp() * 1000))

    report = await run_review(ai=ai, pine=st.pine_source or "", code=st.code or "", params=st.params or {},
                              inst=inst, timeframe=body.timeframe, fetch_candles=candles, tv_csv=body.tv_csv,
                              exchange_id=body.exchange_id)
    st.review = report.model_dump()
    if report.metrics:
        st.metrics = {**report.metrics, "symbol": body.symbol, "timeframe": body.timeframe, "source": "review"}
    if report.passed:
        st.status, st.approved_at = "paper_only", datetime.now(UTC)
    else:
        st.status, st.approved_at = "pending_review", None
    s.add(st)
    s.commit()
    s.refresh(st)
    return report.model_dump()


def _ai_for(model_id: int, s: Session):
    m = s.get(AIModelConfig, model_id) or _404("AI 模型不存在")
    return provider_from_config(m)


@router.post("/strategies/convert-pine")
async def convert_pine_route(body: PineIn, s: Session = Depends(get_session)):
    """貼上 Pine Script → AI 轉換 → 自動審查（四關）→ 通過即進入模擬期"""
    ai = _ai_for(body.ai_model_id, s)
    try:
        result = await convert_pine(ai, body.pine)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    params: dict = {}
    if result.ok:
        params = load_strategy_class(result.code).default_params
    st = StrategyConfig(name=body.name, kind="python", code=result.code, pine_source=body.pine,
                        params=dict(params), status="pending_review")
    s.add(st)
    s.commit()
    s.refresh(st)
    review = await _review_and_save(st, ai, body, s) if result.ok else None
    return {"strategy": strategy_out(st, s), "conversion": result.model_dump(), "review": review}


@router.post("/strategies/{sid}/review")
async def review_strategy(sid: int, body: ReviewIn, s: Session = Depends(get_session)):
    """重新審查（例如補上 TradingView 交易清單 CSV、換交易對 / 週期）"""
    st = s.get(StrategyConfig, sid) or _404()
    if st.kind != "python":
        raise HTTPException(400, "只有自訂 / Pine 轉換的策略需要審查")
    review = await _review_and_save(st, _ai_for(body.ai_model_id, s), body, s)
    return {"strategy": strategy_out(st, s), "review": review}


@router.post("/strategies/{sid}/fix")
async def fix_strategy(sid: int, body: ReviewIn, s: Session = Depends(get_session)):
    """讓 AI 依審查報告修正程式碼，然後重新審查"""
    st = s.get(StrategyConfig, sid) or _404()
    if st.kind != "python" or not st.pine_source:
        raise HTTPException(400, "只有 Pine 轉換的策略可以讓 AI 修正")
    problems = [f"{stg['name']}：{stg['summary']}；" + "；".join(stg.get("details", [])[:6])
                for stg in (st.review or {}).get("stages", []) if stg.get("status") == "failed"]
    if not problems:
        raise HTTPException(400, "審查報告沒有需要修正的問題")
    ai = _ai_for(body.ai_model_id, s)
    try:
        result = await convert_pine(ai, st.pine_source, previous_code=st.code, problems=problems)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    if not result.ok:
        return {"strategy": strategy_out(st, s), "conversion": result.model_dump(), "review": st.review}
    st.code = result.code
    st.params = dict(load_strategy_class(result.code).default_params)
    review = await _review_and_save(st, ai, body, s)
    return {"strategy": strategy_out(st, s), "conversion": result.model_dump(), "review": review}


@router.post("/strategies/{sid}/promote")
def promote_strategy(sid: int, s: Session = Depends(get_session)):
    """手動開放實盤（跳過模擬期剩餘時間）"""
    st = s.get(StrategyConfig, sid) or _404()
    if st.status != "paper_only":
        raise HTTPException(400, "只有模擬期中的策略可以開放實盤")
    st.status = "active"
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st, s)


def _404(msg: str = "找不到資料"):
    raise HTTPException(404, msg)
