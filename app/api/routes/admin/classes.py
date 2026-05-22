"""Routes admin — Gestion des classes par école."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, func

from app.core.deps import AdminUser, DB
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


# ── Import XLSX — format liste-élèves (IEF · COMMUNE · SCHOOL · NIVEAU · Classe) ──

import io as _io
import openpyxl as _openpyxl
from fastapi import File as _File, UploadFile as _UploadFile

@router.post("/import/xlsx")
async def import_classes_xlsx(db: DB, _: AdminUser, file: _UploadFile = _File(...)) -> dict:
    """
    Importe les classes depuis un fichier Excel au format liste-élèves.
    Colonnes utilisées : SCHOOL · NIVEAU · Classe
    Crée les écoles manquantes automatiquement.
    """
    content = await file.read()
    try:
        wb = _openpyxl.load_workbook(_io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Impossible de lire le fichier Excel.")
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not all_rows:
        raise HTTPException(status_code=422, detail="Fichier Excel vide.")

    # ── Mapper les colonnes ────────────────────────────────────────────────────
    raw_header = all_rows[0]
    header = [str(c).strip().lower() if c is not None else "" for c in raw_header]

    COL: dict[str, list[str]] = {
        "school":  ["school", "école", "ecole", "nom_ecole"],
        "niveau":  ["niveau", "level", "niveaux"],
        "classe":  ["classe", "class"],
        "ief":     ["ief"],
        "commune": ["commune", "city", "ville"],
    }
    col_idx: dict[str, int] = {}
    for cname, aliases in COL.items():
        for alias in aliases:
            if alias in header:
                col_idx[cname] = header.index(alias)
                break

    if "school" not in col_idx or "classe" not in col_idx:
        raise HTTPException(
            status_code=422,
            detail="Colonnes requises : 'SCHOOL' et 'Classe'."
        )

    def col(row: tuple, name: str) -> str:
        idx = col_idx.get(name)
        if idx is None or idx >= len(row):
            return ""
        v = row[idx]
        return str(v).strip() if v is not None else ""

    # ── Étape 1 : Agréger les paires (school, classe, niveau) uniques ─────────
    pairs: dict[tuple[str, str], dict] = {}   # key = (SCHOOL_UPPER, CLASSE_UPPER)
    for row in all_rows[1:]:
        school_name = col(row, "school")
        classe_name = col(row, "classe")
        niveau_name = col(row, "niveau")
        if not school_name or not classe_name:
            continue
        key = (school_name.upper(), classe_name.upper())
        if key not in pairs:
            pairs[key] = {
                "school":  school_name,
                "classe":  classe_name,
                "niveau":  niveau_name or "N/A",
                "ief":     col(row, "ief") or None,
                "commune": col(row, "commune") or None,
            }

    if not pairs:
        raise HTTPException(status_code=422, detail="Aucune classe trouvée dans le fichier.")

    # ── Étape 2 : Charger / créer les écoles ──────────────────────────────────
    schools_db = (await db.execute(select(School))).scalars().all()
    school_by_name: dict[str, School] = {s.name.upper(): s for s in schools_db}

    needed_schools = {info["school"].upper() for info in pairs.values()}
    new_schools: list[School] = []
    for s_upper in needed_schools:
        if s_upper not in school_by_name:
            info = next(v for k, v in pairs.items() if k[0] == s_upper)
            s_obj = School(name=info["school"], region=info["ief"], city=info["commune"])
            db.add(s_obj)
            new_schools.append(s_obj)

    if new_schools:
        await db.flush()
        for s in new_schools:
            await db.refresh(s)
            school_by_name[s.name.upper()] = s

    schools_created = len(new_schools)

    # ── Étape 3 : Charger les classes existantes ───────────────────────────────
    existing_classes = (await db.execute(
        select(SchoolClasse.name, SchoolClasse.school_id)
    )).all()
    existing_set: set[tuple[str, str]] = {
        (row.name.upper(), str(row.school_id)) for row in existing_classes
    }

    # ── Étape 4 : Créer les classes manquantes ─────────────────────────────────
    imported = 0
    skipped  = 0
    errors: list[str] = []

    for (school_upper, classe_upper), info in pairs.items():
        school_obj = school_by_name.get(school_upper)
        if not school_obj:
            errors.append(f"École '{info['school']}' introuvable.")
            continue
        if (classe_upper, str(school_obj.id)) in existing_set:
            skipped += 1
            continue
        db.add(SchoolClasse(
            name=info["classe"],
            niveau=info["niveau"],
            school_id=school_obj.id,
        ))
        existing_set.add((classe_upper, str(school_obj.id)))
        imported += 1

    if imported or new_schools:
        await db.flush()

    return {
        "imported":        imported,
        "skipped":         skipped,
        "schools_created": schools_created,
        "errors":          errors,
    }
