"""Garde-fous partagés pour les uploads (taille, type de fichier).

Sans ça, un compte authentifié (admin ou superviseur) pouvait envoyer un
fichier de taille arbitraire, lu entièrement en mémoire avant tout contrôle —
risque d'épuisement disque/mémoire. Caddy plafonne déjà les requêtes à 50MB
en production (voir Caddyfile.api), mais ce garde-fou reste utile en
développement et en défense en profondeur.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 Mo — cohérent avec Caddyfile.api

ALLOWED_RESOURCE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".jpg", ".jpeg", ".png", ".gif",
}
ALLOWED_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".caf"}


async def read_upload_capped(file: UploadFile, *, max_size: int = MAX_UPLOAD_SIZE_BYTES) -> bytes:
    """Lit le contenu d'un UploadFile en rejetant tout fichier dépassant max_size (413)."""
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux (max {max_size // (1024 * 1024)} Mo).",
        )
    return content


def check_extension_allowed(filename: str, allowed: set[str]) -> None:
    """Lève 415 si l'extension du fichier n'est pas dans la liste blanche."""
    ext = Path(filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Type de fichier non autorisé ({ext or 'sans extension'}).",
        )
