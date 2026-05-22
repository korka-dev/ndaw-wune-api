from __future__ import annotations

import csv
import io
import uuid

import openpyxl
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.core.security import hash_password
from app.models.school import School
from app.models.user import User, UserRole, UserStatus
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
    writer.writerow(["nom", "telephone", "titre", "email", "niveau", "classes"])
    for t in items:
        writer.writerow([
            t.name, t.phone or "", t.title or "", t.email or "",
            "|".join(t.niveau or []),
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
        niveau_raw  = (row.get("niveau") or "").strip()
        niveau  = [n.strip() for n in niveau_raw.split("|")  if n.strip()] if niveau_raw  else []
        classes_raw = (row.get("classes") or "").strip()
        classes = [c.strip() for c in classes_raw.split("|") if c.strip()] if classes_raw else []
        body = UserCreate(name=nom, phone=phone, email=email,
                          title=(row.get("titre") or "").strip() or None,
                          role=UserRole.enseignant,
                          niveau=niveau or None,
                          classes=classes or None)
        await user_service.create_user(db, body, force_role=UserRole.enseignant)
        imported += 1
    return {"imported": imported, "errors": errors}


# ── Import XLSX — format liste élèves (IEF, COMMUNE, SCHOOL, enseignant, NIVEAU, Classe, ...) ──

@router.post("/import/xlsx")
async def import_teachers_xlsx(db: DB, _: AdminUser, file: UploadFile = File(...)) -> dict:
    """
    Importe les enseignants depuis un fichier Excel au format liste-élèves.
    Colonnes attendues : IEF · COMMUNE · SCHOOL · enseignant · NIVEAU · Classe
    (les colonnes élèves name / Sexe sont ignorées)

    Pour chaque enseignant unique :
      • L'école est retrouvée par nom (insensible à la casse) ou créée avec IEF+COMMUNE.
      • Le compte enseignant est créé sans téléphone (l'admin peut le renseigner ensuite).
      • Mot de passe par défaut : P@sser123 — must_change_password = True.
    """
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Impossible de lire le fichier Excel.")
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not all_rows:
        raise HTTPException(status_code=422, detail="Fichier Excel vide.")

    # ── Mapper les colonnes ────────────────────────────────────────────────────
    header = [str(c).strip().lower() if c is not None else "" for c in all_rows[0]]

    def col(row: tuple, name: str):
        aliases = {
            "ief":        ["ief"],
            "commune":    ["commune"],
            "school":     ["school", "école", "ecole"],
            "enseignant": ["enseignant", "teacher", "prof"],
            "niveau":     ["niveau", "level", "niveaux"],
            "classe":     ["classe", "class"],
        }
        for alias in aliases.get(name, [name]):
            try:
                idx = header.index(alias)
                v = row[idx]
                return str(v).strip() if v is not None else ""
            except ValueError:
                continue
        return ""

    # ── Étape 1 : Agrégation par enseignant ───────────────────────────────────
    # Clé : (school_name_upper, teacher_name_upper)
    teachers_map: dict[tuple, dict] = {}
    for row in all_rows[1:]:
        teacher_name = col(row, "enseignant")
        school_name  = col(row, "school")
        if not teacher_name or not school_name:
            continue
        key = (school_name.upper(), teacher_name.upper())
        if key not in teachers_map:
            teachers_map[key] = {
                "name":    teacher_name.title(),  # ex: "Astou Seck"
                "school":  school_name,
                "ief":     col(row, "ief"),
                "commune": col(row, "commune"),
                "niveaux": set(),
                "classes": set(),
            }
        niv = col(row, "niveau")
        cls = col(row, "classe")
        if niv:
            teachers_map[key]["niveaux"].add(niv)
        if cls:
            teachers_map[key]["classes"].add(cls)

    if not teachers_map:
        raise HTTPException(
            status_code=422,
            detail="Aucun enseignant trouvé. Vérifiez que le fichier contient les colonnes 'enseignant' et 'SCHOOL'.",
        )

    # ── Étape 2 : Cache écoles existantes ─────────────────────────────────────
    schools_db = (await db.execute(select(School))).scalars().all()
    school_by_name: dict[str, School] = {s.name.upper(): s for s in schools_db}

    schools_created = 0
    imported        = 0
    skipped         = 0
    errors: list[str] = []

    # ── Étape 3 : Itérer les enseignants ──────────────────────────────────────
    for (school_upper, teacher_upper), info in teachers_map.items():

        # — Trouver ou créer l'école —
        school_obj = school_by_name.get(school_upper)
        if school_obj is None:
            school_obj = School(
                name=info["school"],
                region=info["ief"] or None,
                city=info["commune"] or None,
            )
            db.add(school_obj)
            await db.flush()
            await db.refresh(school_obj)
            school_by_name[school_upper] = school_obj
            schools_created += 1

        # — Vérifier si l'enseignant existe déjà (même nom + même école) —
        existing = (await db.execute(
            select(User).where(
                func.upper(User.name) == teacher_upper,
                User.school_id == school_obj.id,
                User.role == UserRole.enseignant,
            )
        )).scalar_one_or_none()

        if existing:
            skipped += 1
            continue

        # — Créer le compte enseignant directement (sans phone/email) —
        try:
            user = User(
                name=info["name"],
                role=UserRole.enseignant,
                status=UserStatus.actif,
                password_hash=hash_password("P@sser123"),
                must_change_password=True,
                school_id=school_obj.id,
                niveau=sorted(info["niveaux"]) if info["niveaux"] else None,
                classes=sorted(info["classes"]) if info["classes"] else None,
            )
            db.add(user)
            await db.flush()
            imported += 1
        except Exception as exc:
            errors.append(f"{info['name']} ({info['school']}) : {exc}")

    return {
        "imported":        imported,
        "skipped":         skipped,
        "schools_created": schools_created,
        "errors":          errors,
    }


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
