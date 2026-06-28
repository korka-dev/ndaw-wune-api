"""Admin — Gestion des dossiers d'évaluation par langue."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Optional

from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, AdminUser
from app.models.evaluation_doc import EvaluationDoc

PANDOC_PATH = "/usr/local/bin/pandoc"
LANGUES_VALIDES = {"Seereer", "Pulaar", "Wolof"}

router = APIRouter(prefix="/evaluation-docs", tags=["Admin — Dossiers d'évaluation"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class EvaluationDocIn(BaseModel):
    langue:     str              = Field(..., max_length=100)
    titre:      str              = Field(..., max_length=255)
    lettres:    list[str]        = Field(default_factory=list)
    syllabes:   list[str]        = Field(default_factory=list)
    mots:       list[str]        = Field(default_factory=list)
    operations: list[str]        = Field(default_factory=list)
    is_active:  bool             = True


class EvaluationDocPatch(BaseModel):
    langue:     Optional[str]        = None
    titre:      Optional[str]        = None
    lettres:    Optional[list[str]]  = None
    syllabes:   Optional[list[str]]  = None
    mots:       Optional[list[str]]  = None
    operations: Optional[list[str]]  = None
    is_active:  Optional[bool]       = None


class EvaluationDocOut(BaseModel):
    id:         str
    langue:     str
    titre:      str
    lettres:    list[str]
    syllabes:   list[str]
    mots:       list[str]
    operations: list[str]
    is_active:  bool
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _to_out(doc: EvaluationDoc) -> EvaluationDocOut:
    return EvaluationDocOut(
        id=str(doc.id),
        langue=doc.langue,
        titre=doc.titre,
        lettres=doc.lettres or [],
        syllabes=doc.syllabes or [],
        mots=doc.mots or [],
        operations=doc.operations or [],
        is_active=doc.is_active,
        created_at=doc.created_at.isoformat(),
        updated_at=doc.updated_at.isoformat(),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[EvaluationDocOut])
async def list_docs(_: AdminUser, db: DB) -> list[EvaluationDocOut]:
    rows = (await db.execute(
        select(EvaluationDoc).order_by(EvaluationDoc.created_at)
    )).scalars().all()
    return [_to_out(d) for d in rows]


@router.post("", response_model=EvaluationDocOut, status_code=status.HTTP_201_CREATED)
async def create_doc(body: EvaluationDocIn, _: AdminUser, db: DB) -> EvaluationDocOut:
    doc = EvaluationDoc(
        langue=body.langue,
        titre=body.titre,
        lettres=body.lettres,
        syllabes=body.syllabes,
        mots=body.mots,
        operations=body.operations,
        is_active=body.is_active,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return _to_out(doc)


@router.get("/{doc_id}", response_model=EvaluationDocOut)
async def get_doc(doc_id: str, _: AdminUser, db: DB) -> EvaluationDocOut:
    doc = await _get_or_404(doc_id, db)
    return _to_out(doc)


@router.patch("/{doc_id}", response_model=EvaluationDocOut)
async def update_doc(doc_id: str, body: EvaluationDocPatch, _: AdminUser, db: DB) -> EvaluationDocOut:
    doc = await _get_or_404(doc_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(doc, field, value)
    await db.flush()
    await db.refresh(doc)
    return _to_out(doc)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_doc(doc_id: str, _: AdminUser, db: DB) -> Response:
    doc = await _get_or_404(doc_id, db)
    await db.delete(doc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/upload", response_model=EvaluationDocOut, status_code=status.HTTP_201_CREATED)
async def upload_doc(file: UploadFile, _: AdminUser, db: DB) -> EvaluationDocOut:
    """Importe et parse un fichier .docx EGRA, puis crée le dossier automatiquement."""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Seuls les fichiers .docx sont acceptés.")

    content = await file.read()

    # Écrire dans un fichier temporaire et parser avec pandoc
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [PANDOC_PATH, tmp_path, "-t", "plain", "--wrap=none"],
            capture_output=True, text=True, timeout=30,
        )
    finally:
        os.unlink(tmp_path)

    if result.returncode != 0:
        raise HTTPException(status_code=422, detail=f"Impossible de lire le document : {result.stderr[:200]}")

    parsed = _parse_egra_text(result.stdout)

    doc = EvaluationDoc(
        langue=parsed["langue"],
        titre=parsed["titre"],
        lettres=parsed["lettres"],
        syllabes=parsed["syllabes"],
        mots=parsed["mots"],
        operations=parsed["operations"],
        is_active=True,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return _to_out(doc)


def _parse_egra_text(text: str) -> dict:
    """Extrait les champs EGRA depuis le texte plain produit par pandoc."""
    lines = [l.strip() for l in text.splitlines()]

    titre = next((l for l in lines if l), "")
    # Déduire la langue depuis le titre (ex: "Test Elève en Seereer")
    m = re.search(r"\b(Seereer|Pulaar|Wolof)\b", titre, re.I)
    langue = m.group(1).capitalize() if m else titre.split()[-1].capitalize()

    def is_instruction(l: str) -> bool:
        return any(k in l.lower() for k in ["demander", "reporter", "résoudre", "effectuer", "poser", "lire", "circuler"])

    def is_section_header(l: str) -> bool:
        return l in ("Lecture", "Mathématiques", "Mathématique", "Maths")

    content_blocks: list[list[str]] = []
    for l in lines:
        if not l or l == titre or is_instruction(l) or is_section_header(l):
            continue
        tokens = [t for t in re.split(r"\s+", l.strip()) if t and not set(t) <= set("-+|= ")]
        if tokens:
            content_blocks.append(tokens)

    lettres    = content_blocks[0] if len(content_blocks) > 0 else []
    syllabes   = content_blocks[1] if len(content_blocks) > 1 else []
    mots: list[str] = []
    operations: list[str] = []

    for block in content_blocks[2:]:
        if all(re.match(r"^\d+[\+\-]\d+=?$", t) for t in block):
            for op in block:
                m2 = re.match(r"(\d+)([\+\-])(\d+)=?", op)
                if m2:
                    operations.append(f"{m2.group(1)} {m2.group(2)} {m2.group(3)} =")
        else:
            mots.extend(block)

    return {
        "langue": langue if langue in LANGUES_VALIDES else langue,
        "titre": titre,
        "lettres": lettres,
        "syllabes": syllabes,
        "mots": mots,
        "operations": operations,
    }


async def _get_or_404(doc_id: str, db: DB) -> EvaluationDoc:
    import uuid
    try:
        uid = uuid.UUID(doc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID invalide.")
    doc = (await db.execute(select(EvaluationDoc).where(EvaluationDoc.id == uid))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return doc
