"""Routes admin — Gestion des classes par école."""
from __future__ import annotations

import io
import uuid
from typing import Optional

import openpyxl
from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select, func

from app.core.deps import AdminUser, DB
from app.core.export_utils import build_xlsx_response
from app.core.pagination import Page, Pagination
from app.models.school_classe import SchoolClasse
from app.models.school import School
from app.schemas.school_classe import SchoolClasseCreate, SchoolClasseResponse, SchoolClasseUpdate

router = APIRouter(prefix="/classes", tags=["Admin — Classes"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(db, classe_id: uuid.UUID) -> SchoolClasse:
    result = await db.execute(
        select(SchoolClasse).where(SchoolClasse.id == classe_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Classe introuvable.")
    return obj


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=Page[SchoolClasseResponse])
async def list_classes(
    db: DB,
    _: AdminUser,
    page: Pagination,
    school_id: Optional[uuid.UUID] = Query(None),
    niveau:    Optional[str]        = Query(None),
) -> Page[SchoolClasseResponse]:
    q = select(SchoolClasse).order_by(SchoolClasse.niveau, SchoolClasse.name)
    if school_id:
        q = q.where(SchoolClasse.school_id == school_id)
    if niveau:
        q = q.where(SchoolClasse.niveau == niveau)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=SchoolClasseResponse, status_code=status.HTTP_201_CREATED)
async def create_classe(body: SchoolClasseCreate, db: DB, _: AdminUser) -> SchoolClasseResponse:
    # Vérifier que l'école existe
    school = (await db.execute(select(School).where(School.id == body.school_id))).scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="École introuvable.")

    # Vérifier unicité (école + nom)
    existing = (await db.execute(
        select(SchoolClasse).where(
            SchoolClasse.school_id == body.school_id,
            SchoolClasse.name == body.name,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"La classe « {body.name} » existe déjà dans cette école."
        )

    obj = SchoolClasse(**body.model_dump())
    db.add(obj)
    await db.flush()
    await db.refresh(obj, attribute_names=["school"])
    return obj


@router.post("/reimport")
async def reimport_classes_xlsx(db: DB, _: AdminUser, file: UploadFile = File(...)) -> dict:
    """
    Réimporte le fichier Excel exporté depuis la plateforme.
    Format attendu : Nom de la classe, Niveau, École associée, Région, Commune

    - Classe existante (même nom + même école) → ignorée
    - Nouvelle classe → créée ; école créée si elle n'existe pas encore
    """
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Impossible de lire le fichier Excel.")
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(all_rows) < 2:
        raise HTTPException(status_code=422, detail="Fichier vide ou sans données.")

    # ── Mapper les colonnes ────────────────────────────────────────────────────
    raw_header = all_rows[0]
    header = [str(c).strip().lower() if c is not None else "" for c in raw_header]

    HEADER_ALIASES: dict[str, list[str]] = {
        "nom":     ["nom de la classe", "nom", "name", "classe"],
        "niveau":  ["niveau", "level"],
        "ecole":   ["école associée", "ecole associee", "école", "ecole", "school"],
        "region":  ["région de l'école (ief)", "region de l'ecole (ief)", "région", "region", "ief"],
        "commune": ["commune / ville", "commune", "ville", "city"],
    }
    col_idx: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in header:
                col_idx[field] = header.index(alias)
                break

    if "nom" not in col_idx:
        raise HTTPException(
            status_code=422,
            detail="Colonne 'Nom de la classe' introuvable. Exportez d'abord depuis la plateforme."
        )
    if "ecole" not in col_idx:
        raise HTTPException(
            status_code=422,
            detail="Colonne 'École associée' introuvable. Exportez d'abord depuis la plateforme."
        )

    def col(row: tuple, name: str) -> str:
        idx = col_idx.get(name)
        if idx is None or idx >= len(row):
            return ""
        v = row[idx]
        return str(v).strip() if v is not None else ""

    # ── Charger les données existantes en 2 requêtes ──────────────────────────
    all_schools = (await db.execute(select(School))).scalars().all()
    school_by_name: dict[str, School] = {s.name.strip().upper(): s for s in all_schools}

    all_classes = (await db.execute(select(SchoolClasse))).scalars().all()
    existing_set: set[tuple[str, str]] = {
        (c.name.strip().upper(), str(c.school_id)) for c in all_classes
    }

    created = 0
    skipped = 0
    errors: list[str] = []
    new_schools: list[School] = []

    for line_num, row in enumerate(all_rows[1:], start=2):
        name   = col(row, "nom")
        ecole  = col(row, "ecole")
        if not name or not ecole:
            skipped += 1
            continue

        # Niveau : colonne si présente, sinon premier mot du nom ("CE1 A" → "CE1")
        niveau = col(row, "niveau") or name.split()[0]

        region  = col(row, "region")  or None
        commune = col(row, "commune") or None

        # Trouver ou créer l'école
        ecole_key = ecole.strip().upper()
        school = school_by_name.get(ecole_key)
        if school is None:
            school = School(
                name=ecole.strip(),
                region=region,
                city=commune,
            )
            db.add(school)
            new_schools.append(school)
            await db.flush()
            await db.refresh(school)
            school_by_name[ecole_key] = school

        # Vérifier si la classe existe déjà
        class_key = (name.strip().upper(), str(school.id))
        if class_key in existing_set:
            skipped += 1
            continue

        try:
            obj = SchoolClasse(
                name=name.strip(),
                niveau=niveau,
                school_id=school.id,
            )
            db.add(obj)
            existing_set.add(class_key)
            created += 1
        except Exception as exc:
            errors.append(f"Ligne {line_num} — {name} ({ecole}) : {exc}")

    if created:
        await db.flush()

    return {
        "created":         created,
        "skipped":         skipped,
        "schools_created": len(new_schools),
        "errors":          errors,
    }


@router.get("/{classe_id}", response_model=SchoolClasseResponse)
async def get_classe(classe_id: uuid.UUID, db: DB, _: AdminUser) -> SchoolClasseResponse:
    return await _get_or_404(db, classe_id)


@router.patch("/{classe_id}", response_model=SchoolClasseResponse)
async def update_classe(
    classe_id: uuid.UUID, body: SchoolClasseUpdate, db: DB, _: AdminUser
) -> SchoolClasseResponse:
    obj = await _get_or_404(db, classe_id)

    data = body.model_dump(exclude_none=True)

    # Si on change le nom, vérifier l'unicité
    new_name = data.get("name", obj.name)
    if new_name != obj.name:
        conflict = (await db.execute(
            select(SchoolClasse).where(
                SchoolClasse.school_id == obj.school_id,
                SchoolClasse.name == new_name,
                SchoolClasse.id != obj.id,
            )
        )).scalar_one_or_none()
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"La classe « {new_name} » existe déjà dans cette école."
            )

    for field, value in data.items():
        setattr(obj, field, value)

    await db.flush()
    await db.refresh(obj, attribute_names=["school"])
    return obj


@router.delete("/{classe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_classe(classe_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    obj = await _get_or_404(db, classe_id)
    await db.delete(obj)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# ── Export Excel ──────────────────────────────────────────────────────────────

@router.get("/export/xlsx")
async def export_classes_xlsx(db: DB, _: AdminUser, fields: Optional[str] = None):
    from sqlalchemy.orm import joinedload

    items = (await db.execute(
        select(SchoolClasse).options(joinedload(SchoolClasse.school)).order_by(SchoolClasse.niveau, SchoolClasse.name)
    )).scalars().all()

    columns = [
        ("nom",     "Nom de la classe",          25),
        ("niveau",  "Niveau",                    15),
        ("ecole",   "École associée",            30),
        ("region",  "Région de l'école (IEF)",   20),
        ("commune", "Commune / Ville",           20),
    ]
    rows = [
        [
            c.name,
            c.niveau or "",
            c.school.name if c.school else "",
            c.school.region if c.school else "",
            c.school.city if c.school else "",
        ]
        for c in items
    ]

    return build_xlsx_response(
        sheet_title="Classes",
        columns=columns,
        rows=rows,
        fields=fields,
        filename="classes.xlsx",
    )
