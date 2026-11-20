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
    """Lit le contenu d'un UploadFile par blocs et s'arrête (413) dès que max_size
    est dépassé — sans jamais charger un fichier démesuré en entier en mémoire."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"Fichier trop volumineux (max {max_size // (1024 * 1024)} Mo).",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def check_extension_allowed(filename: str, allowed: set[str]) -> None:
    """Lève 415 si l'extension du fichier n'est pas dans la liste blanche."""
    ext = Path(filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Type de fichier non autorisé ({ext or 'sans extension'}).",
        )


# ── Classeurs Excel (rapport V2, APP-39) ─────────────────────────────────────
# Un .xlsx est une archive zip : 2 Mo compressés peuvent se décompresser en
# plusieurs Go (« bombe zip »). On contrôle la taille déclarée de chaque entrée
# et le taux de compression AVANT de confier le fichier à openpyxl, on ouvre en
# lecture seule (flux) et on borne le nombre de lignes lues.
MAX_XLSX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 Mo décompressés au total
MAX_XLSX_RATIO = 100                              # compression anormale au-delà
MAX_IMPORT_ROWS = 20_000
MAX_IMPORT_COLUMNS = 200


def load_workbook_safe(content: bytes, *, read_only: bool = True):
    """Ouvre un classeur après contrôle de l'archive. 400 si illisible, 413 si démesuré."""
    import io
    import zipfile

    import openpyxl

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            total = 0
            for info in zf.infolist():
                total += info.file_size
                if info.compress_size and info.file_size / info.compress_size > MAX_XLSX_RATIO and info.file_size > 10 * 1024 * 1024:
                    raise HTTPException(413, "Fichier Excel refusé : contenu compressé anormalement (archive suspecte).")
            if total > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise HTTPException(
                    413, f"Fichier Excel trop volumineux une fois décompressé (max {MAX_XLSX_UNCOMPRESSED_BYTES // (1024 * 1024)} Mo).",
                )
    except zipfile.BadZipFile:
        raise HTTPException(400, "Fichier Excel illisible : seul le format .xlsx est accepté.")
    try:
        return openpyxl.load_workbook(io.BytesIO(content), read_only=read_only, data_only=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Impossible de lire le fichier Excel : {exc}")


def iter_rows_capped(ws, *, max_rows: int = MAX_IMPORT_ROWS, min_row: int = 1):
    """Lignes (valeurs) d'une feuille, 413 au-delà de max_rows lignes de données."""
    for n, row in enumerate(ws.iter_rows(min_row=min_row, values_only=True), start=1):
        if n > max_rows + 1:  # +1 : ligne d'en-tête
            raise HTTPException(413, f"Trop de lignes dans le fichier (max {max_rows}). Découpez l'import.")
        if len(row) > MAX_IMPORT_COLUMNS:
            # Les cellules vides en fin de ligne ne comptent pas (formatage Excel).
            row = tuple(row)
            while row and row[-1] in (None, ""):
                row = row[:-1]
            if len(row) > MAX_IMPORT_COLUMNS:
                raise HTTPException(413, f"Trop de colonnes dans le fichier (max {MAX_IMPORT_COLUMNS}).")
        yield row


class ContentLengthLimitMiddleware:
    """Refuse (413) une requête dont l'en-tête Content-Length dépasse la limite,
    avant toute lecture du corps (ASGI pur, compatible streaming)."""

    def __init__(self, app, max_bytes: int = MAX_UPLOAD_SIZE_BYTES + 1024 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        too_big = int(value) > self.max_bytes
                    except ValueError:
                        too_big = False
                    if too_big:
                        from starlette.responses import JSONResponse
                        resp = JSONResponse(
                            {"detail": f"Requête trop volumineuse (max {self.max_bytes // (1024 * 1024)} Mo)."},
                            status_code=413,
                        )
                        await resp(scope, receive, send)
                        return
                    break
        await self.app(scope, receive, send)
