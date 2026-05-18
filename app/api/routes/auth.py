from __future__ import annotations

import re

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Body
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

from app.core.deps import CurrentUser, bearer_scheme
from app.core.security import create_token_pair, decode_token, is_token_revoked, revoke_token, verify_password

# Instance locale — le wiring global est dans main.py
limiter = Limiter(key_func=get_remote_address)
from app.models.user import User, UserStatus
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["Auth"])


def _phone_variants(digits9: str) -> list[str]:
    """
    Retourne toutes les variantes d'un numéro à 9 chiffres
    telles qu'elles peuvent être stockées en base.
    Ex: 770000000 → ['770000000', '+221770000000', '00221770000000',
                      '221770000000', '77 000 00 00']
    """
    d = digits9
    spaced = f"{d[:2]} {d[2:5]} {d[5:7]} {d[7:9]}" if len(d) == 9 else d
    return [d, f"+221{d}", f"00221{d}", f"221{d}", spaced]


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")          # max 5 tentatives de connexion par IP et par minute
async def login(request: Request, body: LoginRequest = Body(...), db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """
    Connexion par e-mail ou numéro de téléphone.
    Accepte tous les formats de numéro (+221…, 00221…, 9 chiffres, avec espaces).
    La réponse inclut must_change_password=True si c'est la première connexion.
    """
    identifier = body.identifier  # déjà normalisé par le schema

    # Construction des conditions de recherche
    phone_conditions = []
    if "@" not in identifier:
        # C'est un numéro → on essaie toutes les variantes stockées possibles
        for variant in _phone_variants(identifier):
            phone_conditions.append(User.phone == variant)

    result = await db.execute(
        select(User).where(
            or_(
                User.email == identifier,
                *phone_conditions,
            )
        )
    )
    user = result.scalar_one_or_none()

    # Message identique pour un identifiant ou mot de passe incorrect
    # — évite l'énumération de comptes.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiant ou mot de passe incorrect.",
        )
    if user.status == UserStatus.inactif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez l'administrateur.",
        )

    tokens = create_token_pair(str(user.id), user.role.value)
    return TokenResponse(
        **tokens,
        must_change_password=user.must_change_password,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest = Body(...), db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Renouvellement de la paire de tokens via le refresh token."""
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token invalide ou expiré.",
    )
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise invalid
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise invalid
    except JWTError:
        raise invalid

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.status == UserStatus.inactif:
        raise invalid

    tokens = create_token_pair(str(user.id), user.role.value)
    return TokenResponse(**tokens, must_change_password=user.must_change_password)


@router.get("/me", response_model=MeResponse)
async def me(current_user: CurrentUser) -> MeResponse:
    """Retourne le profil de l'utilisateur connecté."""
    return MeResponse.model_validate(current_user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest = Body(...),
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Change le mot de passe de l'utilisateur connecté.
    - Obligatoire à la première connexion (must_change_password=True).
    - Révoque le token actuel après le changement de mot de passe.
    - Après succès : le client doit rediriger vers le login.
    """
    await user_service.change_password(db, current_user, body.new_password)
    # Révoquer le token actuel pour forcer une reconnexion avec le nouveau mot de passe
    await revoke_token(credentials.credentials)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    current_user: CurrentUser,
) -> Response:
    """
    Déconnexion : révoque le token d'accès courant dans la blacklist Redis.
    Le client doit également supprimer ses tokens côté stockage local.
    """
    await revoke_token(credentials.credentials)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("3/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest = Body(...),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Réinitialisation de mot de passe sans authentification.
    Utilisé depuis l'écran 'Mot de passe oublié' de l'app mobile.

    ⚠️  Sans vérification OTP/email, cette route permet à quiconque connaissant
    le numéro de téléphone de changer le mot de passe. À sécuriser avec un OTP
    en production si le contexte le permet.
    """
    identifier = body.identifier

    # Rechercher l'utilisateur par e-mail ou téléphone
    phone_conditions = []
    if "@" not in identifier:
        from app.api.routes.auth import _phone_variants
        for variant in _phone_variants(identifier):
            phone_conditions.append(User.phone == variant)

    result = await db.execute(
        select(User).where(
            or_(User.email == identifier, *phone_conditions)
        )
    )
    user = result.scalar_one_or_none()

    # Réponse identique si l'utilisateur n'existe pas (évite l'énumération)
    if user is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if user.status == UserStatus.inactif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez l'administrateur.",
        )

    await user_service.change_password(db, user, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
