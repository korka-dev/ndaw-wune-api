from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.security import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import invalidate_sync_caches_after_commit
from app.core.security import decode_token, is_token_revoked
from app.models.user import User, UserRole, UserStatus

bearer_scheme = HTTPBearer(auto_error=True)


async def _authenticate(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
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

    # Tokens émis avant un changement/reset de mot de passe sont rejetés,
    # même si non encore expirés (voir User.tokens_valid_from).
    if user.tokens_valid_from is not None:
        iat = payload.get("iat")
        if iat is None or datetime.fromtimestamp(iat, tz=timezone.utc) < user.tokens_valid_from:
            raise credentials_exception

    # Comparer des Enum entre eux, jamais à une string brute
    if user.status == UserStatus.inactif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez l'administrateur.",
        )
    return user


async def get_current_user_pending_ok(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Utilisateur authentifié, MÊME s'il doit encore changer son mot de passe.

    Réservé aux routes nécessaires à l'activation du compte : profil
    (/auth/me), changement de mot de passe et déconnexion.
    """
    return await _authenticate(credentials, db)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Utilisateur authentifié ET activé.

    Un compte dont le mot de passe temporaire n'a pas encore été remplacé
    (must_change_password) est refusé ici : l'activation est imposée côté
    serveur, pas seulement par les écrans de l'application.
    """
    user = await _authenticate(credentials, db)
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PASSWORD_CHANGE_REQUIRED",
        )
    return user


def is_evaluateur(user: User) -> bool:
    """Un évaluateur est aujourd'hui un coordonnateur portant le titre « evaluateur »."""
    return user.role == UserRole.coordonnateur and (user.title or "").strip().lower() == "evaluateur"


def est_superviseur_terrain(user: User) -> bool:
    """Compte qui utilise l'espace superviseur de l'app mobile : rôle superviseur,
    ou coordonnateur (non évaluateur) — la page « Comptes » du dashboard créait ces
    comptes sous le libellé « Superviseur » avec le rôle coordonnateur."""
    if user.role == UserRole.superviseur:
        return True
    return user.role == UserRole.coordonnateur and not is_evaluateur(user)


def espace_mobile(user: User) -> Optional[str]:
    """Espace de l'app mobile où ranger ce compte (None : compte du dashboard seul)."""
    if user.role == UserRole.enseignant:
        return "tuteur"
    if user.role == UserRole.inspecteur:
        return "inspecteur"
    if est_superviseur_terrain(user):
        return "superviseur"
    return None


async def require_admin(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if current_user.role not in (UserRole.admin, UserRole.coordonnateur):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs.",
        )
    # APP-02 : l'évaluateur n'a plus les droits complets d'un coordonnateur.
    # En attendant la matrice des rôles validée par ARED, il consulte (GET)
    # mais ne modifie rien : ni comptes, ni référentiels, ni imports.
    if is_evaluateur(current_user) and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Un compte évaluateur est en lecture seule.",
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
    if not est_superviseur_terrain(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux superviseurs.",
        )
    return current_user


async def require_mobile_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Autorise enseignants ET superviseurs — pour les endpoints partagés (ex: rapports journaliers)."""
    if current_user.role != UserRole.enseignant and not est_superviseur_terrain(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux utilisateurs de l'application mobile.",
        )
    return current_user


async def require_inspecteur(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Inspecteur (IEF) : consultation en lecture seule des écoles de son IEF."""
    if current_user.role != UserRole.inspecteur:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux inspecteurs.",
        )
    return current_user


async def sync_invalidation_on_admin_write(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> None:
    """Toute écriture admin (POST/PUT/PATCH/DELETE) invalide le cache de sync des
    mobiles APRÈS le commit — les listes (élèves, classes, affectations, planning…)
    ne restent plus obsolètes jusqu'à l'expiration du TTL."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        invalidate_sync_caches_after_commit(db)


# ── Aliases typés pour injection dans les routes ──────────────────────────────
CurrentUser      = Annotated[User, Depends(get_current_user)]
CurrentUserPendingOk = Annotated[User, Depends(get_current_user_pending_ok)]
AdminUser        = Annotated[User, Depends(require_admin)]
TeacherUser      = Annotated[User, Depends(require_enseignant)]
SuperviseurUser  = Annotated[User, Depends(require_superviseur)]
MobileUser       = Annotated[User, Depends(require_mobile_user)]
InspecteurUser   = Annotated[User, Depends(require_inspecteur)]


async def require_utilisateur_app(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Tous les comptes de l'application mobile, inspecteur compris (signalements, aide)."""
    if current_user.role not in (UserRole.enseignant, UserRole.inspecteur) and not est_superviseur_terrain(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux utilisateurs de l'application mobile.",
        )
    return current_user


UtilisateurApp   = Annotated[User, Depends(require_utilisateur_app)]
DB               = Annotated[AsyncSession, Depends(get_db)]
