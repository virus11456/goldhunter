from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from goldhunter.core.secrets import api_token


def require_token(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token.encode(), api_token().encode()):
        raise HTTPException(status_code=401, detail="未授權：請提供正確的 API Token")
