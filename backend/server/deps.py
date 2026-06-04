"""Shared FastAPI dependencies (auth via Supabase)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .supabase_client import SupaUser, verify_token

# tokenUrl is informational only (Supabase issues tokens); the browser obtains
# the access token from Supabase Auth and sends it as a Bearer token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


def current_user(token: str | None = Depends(oauth2_scheme)) -> SupaUser:
    user = verify_token(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
