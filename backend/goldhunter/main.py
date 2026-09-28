"""GoldHunter API 入口：uvicorn goldhunter.main:app"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from goldhunter.api import backtest_routes, bot_routes, intel_routes, settings_routes, tradingview_routes
from goldhunter.api.deps import require_token
from goldhunter.config import get_settings
from goldhunter.core.secrets import api_token
from goldhunter.engine.manager import manager
from goldhunter.store.db import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("goldhunter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    get_engine()
    token_file = settings.data_dir / "api_token"
    api_token()
    if not settings.api_token:
        log.info("API Token 存放於 %s（登入介面時使用）", token_file.resolve())
    if settings.resume_bots:
        await manager.resume()
    yield
    # 關閉服務時只停止執行緒，不改資料庫狀態，下次啟動可自動恢復
    for runner in list(manager.runners.values()):
        await runner.stop()


app = FastAPI(title="GoldHunter", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"],
)

auth = [Depends(require_token)]
app.include_router(settings_routes.router, prefix="/api", dependencies=auth, tags=["settings"])
app.include_router(bot_routes.router, prefix="/api", dependencies=auth, tags=["bots"])
app.include_router(backtest_routes.router, prefix="/api", dependencies=auth, tags=["backtest"])
app.include_router(intel_routes.router, prefix="/api", dependencies=auth, tags=["intel"])
app.include_router(tradingview_routes.router, prefix="/api", tags=["tradingview"])


@app.get("/api/health")
def health():
    return {"ok": True}


# 若已建置前端（web/dist），由後端直接提供
_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = _dist / path
        return FileResponse(f if path and f.is_file() else _dist / "index.html")
