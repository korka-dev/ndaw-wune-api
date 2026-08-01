from __future__ import annotations

import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import DB, MobileUser
from app.core.langue import langue_matches
from app.core.security import create_download_token, decode_token, is_token_revoked
from app.models.document import Document
from app.models.school import School
from app.models.user import User, UserRole, UserStatus
from app.schemas.document import DocumentResponse

router = APIRouter(prefix="/ressources", tags=["App — Ressources"])


def _uploads_dir() -> Path:
    return Path(settings.UPLOADS_DIR)


# ── Liste ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[DocumentResponse])
async def list_ressources(db: DB, user: MobileUser) -> List[DocumentResponse]:
    """
    Liste les ressources pédagogiques disponibles pour l'utilisateur : celles
    sans langue définie (communes à toutes les langues) + celles dans la
    langue d'enseignement de son école.
    """
    school_langue: str | None = None
    if user.school_id:
        result = await db.execute(select(School.langue).where(School.id == user.school_id))
        school_langue = result.scalar_one_or_none()

    docs = (await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )).scalars().all()

    # Comparaison via canonical_langue plutôt qu'égalité stricte : les langues
    # sont saisies en texte libre et les orthographes divergent entre écoles et
    # documents ("Pulaar"/"poular", "Sérère"/"seereer"…).
    if school_langue:
        docs = [d for d in docs if not d.langue or langue_matches(d.langue, school_langue)]

    return docs


# ── Téléchargement ─────────────────────────────────────────────────────────────
# Accepte le token en Authorization header (usage in-app normal) OU un jeton
# de téléchargement de courte durée (2 min, scopé à CE document précis) en
# query param ?access_token=… — nécessaire pour les ouvertures via
# Linking.openURL (mobile), le navigateur système ne pouvant pas envoyer de
# header Authorization. On n'accepte PLUS le token d'accès complet (60 min,
# tout le compte) en query string : voir POST /{doc_id}/download-token.

async def _require_mobile_user_or_token(
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> User:
    raw_token: str | None = None
    via_query_param = False
    if credentials:
        raw_token = credentials.credentials
    elif access_token:
        raw_token = access_token
        via_query_param = True

    if not raw_token:
        raise HTTPException(status_code=401, detail="Token manquant.")

    from jose import JWTError  # local import pour garder le fichier léger
    try:
        payload = decode_token(raw_token)
        token_type = payload.get("type")
        if via_query_param:
            if token_type != "download" or payload.get("resource_id") != str(doc_id):
                raise HTTPException(status_code=401, detail="Jeton de téléchargement invalide ou expiré.")
        elif token_type != "access":
            raise HTTPException(status_code=401, detail="Token invalide.")
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token invalide.")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré.")

    if await is_token_revoked(raw_token):
        raise HTTPException(status_code=401, detail="Token révoqué.")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable.")
    if user.status == UserStatus.inactif:
        raise HTTPException(status_code=403, detail="Compte désactivé.")
    if user.role not in (UserRole.enseignant, UserRole.superviseur):
        raise HTTPException(status_code=403, detail="Accès réservé aux utilisateurs mobiles.")
    return user


@router.post("/{doc_id}/download-token")
async def create_ressource_download_token(
    doc_id: uuid.UUID,
    db: DB,
    user: MobileUser,
) -> dict:
    """
    Jeton de téléchargement de courte durée (2 min), scopé à ce document
    précis — à utiliser dans l'URL passée à Linking.openURL quand une app
    externe doit ouvrir le fichier (pas de header Authorization possible).
    Ne jamais utiliser le token d'accès complet pour cet usage.
    """
    result = await db.execute(select(Document).where(Document.id == doc_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    return {"token": create_download_token(str(user.id), str(doc_id)), "expires_in": 120}


@router.get("/{doc_id}/download")
async def download_ressource(
    doc_id: uuid.UUID,
    db: DB,
    _: User = Depends(_require_mobile_user_or_token),
) -> FileResponse:
    """Télécharge une ressource pédagogique (PDF, Excel, CSV…)."""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable.")

    file_path = _uploads_dir() / doc.stored_filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le serveur.")

    return FileResponse(
        path=str(file_path),
        media_type=doc.mime_type,
        filename=doc.original_filename,
    )
