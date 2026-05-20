from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/teachers", tags=["Admin — Enseignants"])


@router.get("", response_model=Page[UserResponse])
async def list_teachers(db: DB, _: AdminUser, page: Pagination) -> Page[UserResponse]:
    total, items = await user_service.list_by_role(db, UserRole.enseignant, page.skip, page.limit)
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_teacher(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    # force_role garantit que le rôle est toujours "enseignant", peu importe la valeur envoyée
    return await user_service.create_user(db, body, force_role=UserRole.enseignant)


# ── Export CSV — DOIT être avant /{teacher_id} pour éviter qu'FastAPI confonde "export" avec un UUID ──

@router.get("/export/csv")
async def export_teachers_csv(db: DB, _: AdminUser) -> StreamingResponse:
    items = (await db.execute(
        select(User).where(User.role == UserRole.enseignant).order_by(User.name)
    )).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nom", "telephone", "titre", "email", "classes"])
    for t in items:
        writer.writerow([
            t.name, t.phone or "", t.title or "", t.email or "",
            "|".join(t.classes or []),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=enseignants.csv"},
    )


# ── Import CSV ────────────────────────────────────────────────────────────────

@router.post("/import/csv")
async def import_teachers_csv(db: DB, _: AdminUser, file: UploadFile = File(...)) -> dict:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    headers = {f.lower().strip() for f in (reader.fieldnames or [])}
    if "nom" not in headers:
        raise HTTPException(status_code=422, detail="Colonne 'nom' requise.")

    imported = 0
    errors: list[str] = []
    for i, row in enumerate(reader, start=2):
        nom = (row.get("nom") or "").strip()
        phone = (row.get("telephone") or row.get("phone") or "").strip() or None
        email = (row.get("email") or "").strip() or None
        if not nom or (not phone and not email):
            errors.append(f"Ligne {i} : nom + (téléphone ou email) requis.")
            continue
        classes_raw = (row.get("classes") or "").strip()
        classes = [c.strip() for c in classes_raw.split("|") if c.strip()] if classes_raw else []
        body = UserCreate(name=nom, phone=phone, email=email,
                          title=(row.get("titre") or "").strip() or None,
                          role=UserRole.enseignant, classes=classes or None)
        await user_service.create_user(db, body, force_role=UserRole.enseignant)
        imported += 1
    return {"imported": imported, "errors": errors}


# ── Routes paramétrées — après les routes fixes ──────────────────────────────

@router.get("/{teacher_id}", response_model=UserResponse)
async def get_teacher(teacher_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.get_by_id(db, teacher_id)


@router.patch("/{teacher_id}", response_model=UserResponse)
async def update_teacher(teacher_id: uuid.UUID, body: UserUpdate, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.update_user(db, teacher_id, body)


@router.delete("/{teacher_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_teacher(teacher_id: uuid.UUID, db: DB, current_user: AdminUser) -> Response:
    await user_service.delete_user(db, teacher_id, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{teacher_id}/toggle-status", response_model=UserResponse)
async def toggle_status(teacher_id: uuid.UUID, db: DB, _: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, teacher_id)
