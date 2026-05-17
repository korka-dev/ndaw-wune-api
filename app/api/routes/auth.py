from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response, status
from jose import JWTError
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, DB
from app.core.security import create_token_pair, decode_token, verify_password
from app.models.user import User, UserStatus
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
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
async def login(body: LoginRequest, db: DB) -> TokenResponse:
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
async def refresh(body: RefreshRequest, db: DB) -> TokenResponse:
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
    body: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DB,
) -> Response:
    """
    Change le mot de passe de l'utilisateur connecté.
    - Obligatoire à la première connexion (must_change_password=True).
    - Après succès : le client doit déconnecter l'utilisateur et le rediriger
      vers le login pour qu'il se reconnecte avec son nouveau mot de passe.
    """
    await user_service.change_password(db, current_user, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
