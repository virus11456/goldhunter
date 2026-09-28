"""設定介面：交易所帳戶、AI 模型、策略（含 Pine Script 轉換）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from goldhunter.ai.base import AIProviderError
from goldhunter.ai.registry import available_providers
from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.secrets import decrypt, encrypt, mask
from goldhunter.engine.manager import exchange_from_account, provider_from_config
from goldhunter.exchanges.registry import available_exchanges
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import AIModelConfig, Bot, ExchangeAccount, StrategyConfig, get_session
from goldhunter.strategies.custom import TEMPLATE, StrategyCodeError, load_strategy_class, validate_code
from goldhunter.strategies.registry import BUILTIN, list_strategy_types
from goldhunter.tradingview.pine_converter import convert_pine

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


def strategy_out(st: StrategyConfig) -> dict:
    return st.model_dump()


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
    return [strategy_out(x) for x in s.exec(select(StrategyConfig))]


@router.post("/strategies")
def create_strategy(body: StrategyIn, s: Session = Depends(get_session)):
    _check_strategy(body)
    st = StrategyConfig(name=body.name, kind=body.kind, params=body.params, code=body.code)
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st)


@router.put("/strategies/{sid}")
def update_strategy(sid: int, body: StrategyIn, s: Session = Depends(get_session)):
    st = s.get(StrategyConfig, sid) or _404()
    _check_strategy(body)
    if body.code != st.code and body.kind == "python":
        st.status = "pending_review"  # 程式碼有改就要重新審核
    st.name, st.kind, st.params, st.code = body.name, body.kind, body.params, body.code
    s.add(st)
    s.commit()
    s.refresh(st)
    return strategy_out(st)


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
    return strategy_out(st)


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


class PineIn(BaseModel):
    name: str
    pine: str
    ai_model_id: int


@router.post("/strategies/convert-pine")
async def convert_pine_route(body: PineIn, s: Session = Depends(get_session)):
    m = s.get(AIModelConfig, body.ai_model_id) or _404("AI 模型不存在")
    try:
        result = await convert_pine(provider_from_config(m), body.pine)
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
    return {"strategy": strategy_out(st), "conversion": result.model_dump()}


def _404(msg: str = "找不到資料"):
    raise HTTPException(404, msg)
