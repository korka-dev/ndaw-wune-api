from __future__ import annotations

import os
import uuid
import mimetypes
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select, delete

from app.core.config import settings
from app.core.deps import AdminUser, DB
from app.models.document import Document
from app.schemas.document import DocumentResponse

router = APIRouter(prefix="/ressources", tags=["Admin — Ressources"])


def _uploads_dir() -> Path:
    """Répertoire de stockage des fichiers uploadés (créé automatiquement)."""
    d = Path(settings.UPLOADS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Liste ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[DocumentResponse])
async def list_documents(db: DB, _: AdminUser) -> List[DocumentResponse]:
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )
    return result.scalars().all()


# ── Upload ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    db: DB,
    current_user: AdminUser,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    description: str | None = Form(default=None),
) -> DocumentResponse:
    content = await file.read()
    file_size = len(content)

    # Nom original nettoyé (garde uniquement le nom de base, sans chemin)
    original_filename = Path(file.filename or "fichier").name

    # Type MIME : d'abord celui envoyé par le navigateur, sinon on le détecte
    mime_type = file.content_type or ""
    if not mime_type or mime_type == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(original_filename)
        if guessed:
            mime_type = guessed
    if not mime_type:
        mime_type = "application/octet-stream"

    # Extension d'origine conservée pour le fichier stocké
    suffix = Path(original_filename).suffix
    stored_filename = f"{uuid.uuid4()}{suffix}"

    # Écriture sur disque
    dest = _uploads_dir() / stored_filename
    dest.write_bytes(content)

    # Titre par défaut = nom du fichier sans extension
    effective_title = (title or "").strip() or Path(original_filename).stem

    doc = Document(
        title=effective_title,
        original_filename=original_filename,
        stored_filename=stored_filename,
        mime_type=mime_type,
        file_size=file_size,
        description=(description or "").strip() or None,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


# ── Téléchargement ─────────────────────────────────────────────────────────────

@router.get("/{doc_id}/download")
async def download_document(doc_id: uuid.UUID, db: DB, _: AdminUser) -> FileResponse:
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


# ── Suppression ────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable.")

    # Supprimer le fichier physique (sans erreur si déjà absent)
    file_path = _uploads_dir() / doc.stored_filename
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass

    await db.execute(delete(Document).where(Document.id == doc_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
