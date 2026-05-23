from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token, is_token_revoked
from app.models.user import User, UserRole, UserStatus

bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalide ou expiré.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Vérifier la blacklist Redis (révocation explicite : logout, changement mdp)
    if await is_token_revoked(credentials.credentials):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # Comparer des Enum entre eux, jamais à une string brute
    if user.status == UserStatus.inactif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez l'administrateur.",
        )
    return user


async def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role not in (UserRole.admin, UserRole.coordonnateur):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs.",
        )
    return current_user


async def require_enseignant(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role != UserRole.enseignant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux enseignants.",
        )
    return current_user


async def require_superviseur(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Réservé aux superviseurs de terrain (app mobile — onglets superviseur)."""
    if current_user.role != UserRole.superviseur:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux superviseurs.",
        )
    return current_user


async def require_mobile_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Autorise enseignants ET superviseurs — pour les endpoints partagés (ex: rapports journaliers)."""
    if current_user.role not in (UserRole.enseignant, UserRole.superviseur):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux utilisateurs de l'application mobile.",
        )
    return current_user


# ── Aliases typés pour injection dans les routes ──────────────────────────────
CurrentUser      = Annotated[User, Depends(get_current_user)]
AdminUser        = Annotated[User, Depends(require_admin)]
TeacherUser      = Annotated[User, Depends(require_enseignant)]
SuperviseurUser  = Annotated[User, Depends(require_superviseur)]
MobileUser       = Annotated[User, Depends(require_mobile_user)]
DB               = Annotated[AsyncSession, Depends(get_db)]
