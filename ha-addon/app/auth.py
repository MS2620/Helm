from __future__ import annotations

import hmac
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


async def verify_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    settings = request.app.state.settings
    expected = (settings.app_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server app_api_token is not configured.",
        )

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials.strip(), expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
