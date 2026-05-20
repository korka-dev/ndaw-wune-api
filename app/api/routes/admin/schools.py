import csv
import io
import uuid
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.school import School
from app.schemas.school import SchoolCreate, SchoolUpdate, SchoolResponse

router = APIRouter(prefix="/schools", tags=["Admin — Écoles"])


async def _check_phone_unique(db, phone: str | None, exclude_id: uuid.UUID | None = None) -> None:
    """Lève 409 si le numéro de téléphone est déjà utilisé par une autre école."""
    if not phone:
        return
    q = select(School).where(School.director_phone == phone)
    if exclude_id:
        q = q.where(School.id != exclude_id)
    existing = await db.scalar(q)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Le numéro {phone} est déjà associé à l'école « {existing.name} ».",
        )


@router.get("", response_model=Page[SchoolResponse])
async def list_schools(db: DB, _: AdminUser, page: Pagination) -> Page[SchoolResponse]:
    base  = select(School).order_by(School.name)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=SchoolResponse, status_code=status.HTTP_201_CREATED)
async def create_school(body: SchoolCreate, db: DB, _: AdminUser):
    await _check_phone_unique(db, body.director_phone)
    school = School(**body.model_dump())
    db.add(school)
    await db.commit()
    await db.refresh(school)
    return school


# ── Export CSV — DOIT être avant /{school_id} pour éviter qu'FastAPI confonde "export" avec un UUID ──

@router.get("/export/csv")
async def export_schools_csv(db: DB, _: AdminUser) -> StreamingResponse:
    items = (await db.execute(select(School).order_by(School.name))).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nom", "region", "commune", "directeur", "telephone_directeur"])
    for s in items:
        writer.writerow([s.name, s.region or "", s.city or "", s.director or "", s.director_phone or ""])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ecoles.csv"},
    )


# ── Import CSV ────────────────────────────────────────────────────────────────

@router.post("/import/csv")
async def import_schools_csv(db: DB, _: AdminUser, file: UploadFile = File(...)) -> dict:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    if "nom" not in {f.lower().strip() for f in (reader.fieldnames or [])}:
        raise HTTPException(status_code=422, detail="Colonne 'nom' requise.")
    imported = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        nom = (row.get("nom") or "").strip()
        if not nom:
            errors.append(f"Ligne {i} : nom manquant.")
            continue
        school = School(
            name=nom,
            region=(row.get("region") or "").strip() or None,
            city=(row.get("commune") or row.get("city") or "").strip() or None,
            director=(row.get("directeur") or row.get("director") or "").strip() or None,
            director_phone=(row.get("telephone_directeur") or row.get("director_phone") or "").strip() or None,
        )
        db.add(school)
        imported += 1
    if imported:
        await db.commit()
    return {"imported": imported, "errors": errors}


# ── Routes paramétrées — après les routes fixes ──────────────────────────────

@router.get("/{school_id}", response_model=SchoolResponse)
async def get_school(school_id: uuid.UUID, db: DB, _: AdminUser):
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="École introuvable.")
    return school


@router.patch("/{school_id}", response_model=SchoolResponse)
async def update_school(school_id: uuid.UUID, body: SchoolUpdate, db: DB, _: AdminUser):
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="École introuvable.")
    await _check_phone_unique(db, body.director_phone, exclude_id=school_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(school, field, value)
    await db.commit()
    await db.refresh(school)
    return school


@router.delete("/{school_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_school(school_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise HTTPException(status_code=404, detail="École introuvable.")
    await db.delete(school)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
