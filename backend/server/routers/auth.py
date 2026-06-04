"""Auth routes. Sign-up / sign-in happen on the client via Supabase Auth;
the backend only reports the authenticated identity for the current token."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import current_user
from ..schemas import UserOut
from ..supabase_client import SupaUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=UserOut)
def me(user: SupaUser = Depends(current_user)):
    return UserOut(id=user.id, email=user.email)
