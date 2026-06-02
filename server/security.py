"""Password hashing and JWT issuing/verifying."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .settings import server_settings


def hash_password(password: str) -> str:
    # bcrypt operates on the first 72 bytes; truncate explicitly to be safe.
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=server_settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, server_settings.jwt_secret, algorithm=server_settings.jwt_algorithm)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(
            token, server_settings.jwt_secret, algorithms=[server_settings.jwt_algorithm]
        )
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
