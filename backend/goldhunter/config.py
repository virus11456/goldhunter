from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOLDHUNTER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    # 單人使用：所有 /api 請求都要帶 Authorization: Bearer <api_token>
    # 留空則首次啟動自動產生並寫入 data_dir/api_token
    api_token: str = ""
    # AES-256-GCM 金鑰（base64），用來加密存放交易所 / AI 的 API Key
    # 留空則首次啟動自動產生並寫入 data_dir/secret.key
    secret_key: str = ""
    # 服務重啟時是否自動恢復原本在執行的 Bot
    resume_bots: bool = True
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'goldhunter.db'}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
