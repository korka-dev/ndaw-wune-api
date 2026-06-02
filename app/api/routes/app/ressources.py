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
from app.core.security import decode_token, is_token_revoked
from app.models.document import Document
from app.models.user import User, UserRole, UserStatus
from app.schemas.document import DocumentResponse

router = APIRouter(prefix="/ressources", tags=["App — Ressources"])


def _uploads_dir() -> Path:
    return Path(settings.UPLOADS_DIR)


# ── Liste ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[DocumentResponse])
async def list_ressources(db: DB, _: MobileUser) -> List[DocumentResponse]:
    """Liste toutes les ressources pédagogiques disponibles."""
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


# ── Téléchargement ─────────────────────────────────────────────────────────────
# Accepte le token en Authorization header OU en query param ?access_token=…
# Le query param est nécessaire pour les ouvertures via Linking.openURL (mobile)
# car le navigateur système ne peut pas envoyer de header Authorization.

async def _require_mobile_user_or_token(
    db: AsyncSession = Depends(get_db),
    access_token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(
        HTTPBearer(auto_error=False)
    ),
) -> User:
    raw_token: str | None = None
    if credentials:
        raw_token = credentials.credentials
    elif access_token:
        raw_token = access_token

    if not raw_token:
        raise HTTPException(status_code=401, detail="Token manquant.")

    from jose import JWTError  # local import pour garder le fichier léger
    try:
        payload = decode_token(raw_token)
        if payload.get("type") != "access":
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
