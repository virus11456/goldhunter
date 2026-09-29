"""投資大師：列表、上傳（nuwa-skill 的 SKILL.md / zip）、系統內蒸餾（先估費用）、保真度評分、開放實盤。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from goldhunter.ai.base import AIProviderError
from goldhunter.engine.manager import provider_from_config
from goldhunter.personas import onchain
from goldhunter.personas.distill import PASS_SCORE, distill, estimate_cost, fidelity, parse_upload
from goldhunter.personas.lifecycle import STATUS_LABEL, _bots_using, maybe_promote, persona_progress, seed_builtin
from goldhunter.store.db import AIModelConfig, Persona, get_session

router = APIRouter()


def persona_out(p: Persona, s: Session, full: bool = False) -> dict:
    d = p.model_dump(exclude=set() if full else {"profile"})
    d["status_label"] = STATUS_LABEL.get(p.status, p.status)
    d["paper_progress"] = persona_progress(p, s)
    d["profile_chars"] = len(p.profile or "")
    d["used_by_bots"] = len(_bots_using(p, s, paper_only=False))
    d["pass_score"] = PASS_SCORE
    return d


def _ai(model_id: int, s: Session):
    m = s.get(AIModelConfig, model_id)
    if not m:
        raise HTTPException(404, "AI 模型不存在")
    return m, provider_from_config(m)


@router.get("/personas")
def list_personas(s: Session = Depends(get_session)):
    seed_builtin(s)
    out = []
    for p in s.exec(select(Persona)).all():
        maybe_promote(p, s)
        out.append(persona_out(p, s))
    return out


@router.get("/personas/{pid}")
def get_persona(pid: int, s: Session = Depends(get_session)):
    p = s.get(Persona, pid) or _404()
    return persona_out(p, s, full=True)


class UploadIn(BaseModel):
    filename: str
    content_base64: str
    role: str = "trader"
    markets: list[str] = ["crypto"]


@router.post("/personas/upload")
def upload_persona(body: UploadIn, s: Session = Depends(get_session)):
    """上傳在別處蒸餾好的檔案（例如用 Claude Code + nuwa-skill 產出的 SKILL.md 或 zip），不花本系統 AI 費用"""
    try:
        data = base64.b64decode(body.content_base64)
    except ValueError as e:
        raise HTTPException(400, "檔案內容格式錯誤") from e
    if len(data) > 10_000_000:
        raise HTTPException(400, "檔案太大（上限 10 MB）")
    try:
        parsed = parse_upload(body.filename, data)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # 壞掉的 zip 等
        raise HTTPException(400, f"無法讀取檔案：{type(e).__name__}") from e
    p = Persona(name=parsed.name, role=body.role, markets=body.markets, summary=parsed.description[:200],
                profile=parsed.profile, source="upload",
                meta={"files": parsed.files[:50], "truncated": parsed.truncated, "filename": body.filename})
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


class DistillIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    ai_model_id: int
    depth: str = "standard"  # quick / standard
    corpus: str | None = None  # 使用者提供的一手資料（書摘、訪談稿…）
    hyperliquid_address: str | None = None  # 加密貨幣交易員的公開錢包，抓真實交易紀錄
    role: str = "trader"
    markets: list[str] = ["crypto"]


@router.post("/personas/estimate")
def estimate(body: DistillIn, s: Session = Depends(get_session)):
    """蒸餾前先估費用（含保真度評分）"""
    m, _ = _ai(body.ai_model_id, s)
    return {**estimate_cost(m.provider, m.model or "claude-opus-5", body.depth), "model": m.model or m.provider}


@router.post("/personas/distill")
async def distill_persona(body: DistillIn, s: Session = Depends(get_session)):
    m, ai = _ai(body.ai_model_id, s)
    corpus = body.corpus or ""
    onchain_summary = None
    if body.hyperliquid_address:
        try:
            fills = await onchain.fetch_fills(body.hyperliquid_address.strip())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"無法取得 Hyperliquid 交易紀錄：{type(e).__name__}") from e
        onchain_summary = onchain.summarize_fills(fills)
        corpus = onchain.summary_to_corpus(body.hyperliquid_address, onchain_summary) + "\n\n" + corpus
    try:
        profile = await distill(ai, body.name, body.depth, corpus or None)
        report = await fidelity(ai, body.name, profile)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    if len(profile) < 300:
        raise HTTPException(502, "蒸餾結果太短，請改用標準檔位或提供一手資料後重試")
    p = Persona(name=body.name, role=body.role, markets=body.markets, profile=profile, source="distill",
                fidelity=report.model_dump(),
                meta={"depth": body.depth, "model": m.model, "onchain": onchain_summary,
                      "hyperliquid_address": body.hyperliquid_address,
                      "estimate": estimate_cost(m.provider, m.model or "", body.depth)})
    _apply_fidelity(p, report.passed)
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


class FidelityIn(BaseModel):
    ai_model_id: int


@router.post("/personas/{pid}/fidelity")
async def run_fidelity(pid: int, body: FidelityIn, s: Session = Depends(get_session)):
    """保真度評分：出題 / 作答 / 評分三次獨立 AI 呼叫；≥ 70 分進入模擬期"""
    p = s.get(Persona, pid) or _404()
    _, ai = _ai(body.ai_model_id, s)
    try:
        report = await fidelity(ai, p.name, p.profile)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    p.fidelity = report.model_dump()
    _apply_fidelity(p, report.passed)
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


def _apply_fidelity(p: Persona, passed: bool) -> None:
    if passed and p.status == "draft":
        p.status, p.approved_at = "paper_only", datetime.now(UTC)
    elif not passed and p.status != "active":
        p.status, p.approved_at = "draft", None


class PersonaUpdate(BaseModel):
    name: str
    role: str = "trader"
    markets: list[str] = ["crypto"]
    summary: str = ""
    profile: str


@router.put("/personas/{pid}")
def update_persona(pid: int, body: PersonaUpdate, s: Session = Depends(get_session)):
    p = s.get(Persona, pid) or _404()
    if body.profile != p.profile:
        p.status, p.fidelity, p.approved_at = "draft", {}, None  # 內容改了要重新評分
    p.name, p.role, p.markets, p.summary, p.profile = body.name, body.role, body.markets, body.summary, body.profile
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


@router.post("/personas/{pid}/promote")
def promote_persona(pid: int, s: Session = Depends(get_session)):
    """手動開放實盤（跳過模擬期剩餘時間）"""
    p = s.get(Persona, pid) or _404()
    if p.status != "paper_only":
        raise HTTPException(400, "只有保真度通過、模擬期中的大師可以開放實盤")
    p.status = "active"
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s)


@router.delete("/personas/{pid}")
def delete_persona(pid: int, s: Session = Depends(get_session)):
    p = s.get(Persona, pid) or _404()
    if p.source == "builtin":
        raise HTTPException(400, "內建大師不能刪除")
    if _bots_using(p, s, paper_only=False):
        raise HTTPException(400, "仍有 Bot 使用這位大師")
    s.delete(p)
    s.commit()
    return {"ok": True}


def _404(msg: str = "找不到這位大師"):
    raise HTTPException(404, msg)
