"""Endpoints Admin — Élèves."""
from __future__ import annotations

import csv
import io
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.eleve import Eleve
from app.schemas.eleve import EleveCreate, EleveUpdate, EleveResponse

router = APIRouter(prefix="/eleves", tags=["Admin — Élèves"])


@router.get("", response_model=Page[EleveResponse])
async def list_eleves(
    db: DB,
    _: AdminUser,
    page: Pagination,
    school_id:  Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None,
    classe:     Optional[str]       = None,
) -> Page[EleveResponse]:
    base = select(Eleve).order_by(Eleve.nom)
    if school_id:
        base = base.where(Eleve.school_id == school_id)
    if session_id:
        base = base.where(Eleve.session_id == session_id)
    if classe:
        base = base.where(Eleve.classe == classe)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=EleveResponse, status_code=status.HTTP_201_CREATED)
async def create_eleve(body: EleveCreate, db: DB, _: AdminUser) -> EleveResponse:
    eleve = Eleve(**body.model_dump())
    db.add(eleve)
    await db.flush()
    await db.refresh(eleve)
    return eleve


@router.patch("/{eleve_id}", response_model=EleveResponse)
async def update_eleve(eleve_id: uuid.UUID, body: EleveUpdate, db: DB, _: AdminUser) -> EleveResponse:
    result = await db.execute(select(Eleve).where(Eleve.id == eleve_id))
    eleve = result.scalar_one_or_none()
    if not eleve:
        raise HTTPException(status_code=404, detail="Élève introuvable.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(eleve, field, value)
    await db.flush()
    await db.refresh(eleve)
    return eleve


@router.delete("/{eleve_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_eleve(eleve_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    result = await db.execute(select(Eleve).where(Eleve.id == eleve_id))
    eleve = result.scalar_one_or_none()
    if not eleve:
        raise HTTPException(status_code=404, detail="Élève introuvable.")
    await db.delete(eleve)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Export CSV ────────────────────────────────────────────────────────────────

@router.get("/export/csv")
async def export_eleves_csv(db: DB, _: AdminUser) -> StreamingResponse:
    items = (await db.execute(select(Eleve).order_by(Eleve.nom))).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nom", "prenom", "sexe", "date_naissance", "classe", "statut"])
    for e in items:
        writer.writerow([
            e.nom, e.prenom or "", e.genre or "", e.date_naissance or "",
            e.classe, e.statut,
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=eleves.csv"},
    )


# ── Import CSV ────────────────────────────────────────────────────────────────

@router.post("/import/csv")
async def import_eleves_csv(db: DB, _: AdminUser, file: UploadFile = File(...)) -> dict:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    required = {"nom", "classe"}
    if not required.issubset({f.lower().strip() for f in (reader.fieldnames or [])}):
        raise HTTPException(status_code=422, detail="Colonnes requises : nom, classe")

    imported = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        nom    = (row.get("nom") or "").strip()
        classe = (row.get("classe") or "").strip()
        if not nom or not classe:
            errors.append(f"Ligne {i} : nom ou classe manquant.")
            continue
        eleve = Eleve(
            nom=nom,
            prenom=(row.get("prenom") or "").strip() or None,
            classe=classe,
            genre=(row.get("sexe") or row.get("genre") or "").strip() or None,
            date_naissance=(row.get("date_naissance") or "").strip() or None,
            statut=(row.get("statut") or "actif").strip(),
        )
        db.add(eleve)
        imported += 1

    if imported:
        await db.flush()
    return {"imported": imported, "errors": errors}
