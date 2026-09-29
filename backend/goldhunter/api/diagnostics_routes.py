"""連線檢查 API：一鍵測試交易所、AI 模型、資料來源（只讀取、不下單）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from goldhunter.diagnostics import run_all
from goldhunter.store.db import get_session

router = APIRouter()


class CheckIn(BaseModel):
    include_ai: bool = True  # AI 模型會送一次極短的請求（費用極少）


@router.post("/diagnostics")
async def diagnostics(body: CheckIn, s: Session = Depends(get_session)):
    return await run_all(s, include_ai=body.include_ai)
