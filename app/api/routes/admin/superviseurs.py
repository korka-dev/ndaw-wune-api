"""
Endpoints Admin — Superviseurs de terrain (rôle superviseur).
Les superviseurs suivent les enseignants sur le terrain depuis l'app mobile.

Rétrocompatibilité : la liste inclut les anciens enregistrements stockés
avec le rôle 'coordonnateur' (avant la migration a9f1b2c3d4e5).
"""
from __future__ import annotations

import csv
import io
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from sqlalchemy import or_

from app.core.deps import AdminUser, DB
from app.core.export_utils import build_xlsx_response, sanitize_cell
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.models.school import School
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service
from app.services import affectation_superviseurs_service
from app.services.audit_service import journaliser
from app.core.upload_utils import read_upload_capped
from app.services.archivage import non_archive

router = APIRouter(prefix="/superviseurs", tags=["Admin — Superviseurs"])

# Rôles considérés comme "superviseur" (rétrocompat : anciens comptes coordonnateur)
_SUPERVISEUR_ROLES = (UserRole.superviseur, UserRole.coordonnateur)


@router.get("", response_model=Page[UserResponse])
async def list_superviseurs(db: DB, _: AdminUser, page: Pagination, archives: bool = Query(False, description="Inclure les éléments archivés")) -> Page[UserResponse]:
    """Liste les superviseurs (rôle superviseur + anciens coordonnateurs non-évaluateurs)."""
    from sqlalchemy import func, select
    base = (
        select(User)
        .where(
            or_(
                User.role == UserRole.superviseur,
                (User.role == UserRole.coordonnateur) & (User.title != "evaluateur"),
            ),
            non_archive(User, archives),
        )
        .options(selectinload(User.school))
        .order_by(User.name)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_superviseur(body: UserCreate, db: DB, _: AdminUser) -> UserResponse:
    """Crée un superviseur avec le rôle 'superviseur' (app mobile — onglets superviseur)."""
    return await user_service.create_user(db, body, force_role=UserRole.superviseur)


def _superviseur_filter():
    return or_(
        User.role == UserRole.superviseur,
        (User.role == UserRole.coordonnateur) & (User.title != "evaluateur"),
    )


@router.get("/export/csv")
async def export_superviseurs_csv(db: DB, _: AdminUser) -> StreamingResponse:
    """Export CSV de tous les superviseurs avec leur école et nb d'enseignants assignés."""
    items = (await db.execute(
        select(User)
        .where(_superviseur_filter())
        .options(selectinload(User.school))
        .order_by(User.name)
    )).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["nom", "telephone", "ecole", "code_ecole", "nb_enseignants", "statut"])
    for s in items:
        writer.writerow([sanitize_cell(v) for v in (
            s.name,
            s.phone or "",
            s.school.name if s.school else "",
            s.school.code_ecole if s.school and s.school.code_ecole is not None else "",
            len(s.classes or []),
            s.status,
        )])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=superviseurs.csv"},
    )


@router.get("/export/xlsx")
async def export_superviseurs_xlsx(db: DB, _: AdminUser, fields: Optional[str] = None) -> StreamingResponse:
    """Export Excel des superviseurs."""
    items = (await db.execute(
        select(User)
        .where(_superviseur_filter())
        .options(selectinload(User.school))
        .order_by(User.name)
    )).scalars().all()

    columns = [
        ("nom",             "Nom complet",          30),
        ("telephone",       "Téléphone",            18),
        ("ecole",           "École",                35),
        ("code_ecole",      "Code école",           12),
        ("nb_enseignants",  "Nb enseignants",       15),
        ("statut",          "Statut",               12),
    ]
    rows = [
        [
            s.name,
            s.phone or "",
            s.school.name if s.school else "",
            s.school.code_ecole if s.school and s.school.code_ecole is not None else "",
            len(s.classes or []),
            s.status,
        ]
        for s in items
    ]
    return build_xlsx_response(
        sheet_title="Superviseurs",
        columns=columns,
        rows=rows,
        fields=fields,
        filename="superviseurs.xlsx",
    )


@router.post("/import-affectations")
async def importer_affectations(
    db: DB,
    admin: AdminUser,
    file: UploadFile = File(...),
    dry_run: bool = Query(True, description="Aperçu : rien n'est enregistré."),
    remplacer: bool = Query(False, description="Remplace les affectations existantes au lieu d'ajouter."),
) -> dict:
    """Affecte à chaque superviseur ses tuteurs depuis l'onglet « Classes » de la
    base de l'année (Superviseur proposé → Enseignant proposé, par téléphone).
    Le fichier est lu en mémoire, jamais stocké."""
    lignes = affectation_superviseurs_service.lire(await read_upload_capped(file))
    r = await affectation_superviseurs_service.traiter(db, lignes, remplacer=remplacer)
    if dry_run:
        await db.rollback()
    elif r["affectes"]:
        journaliser(db, acteur=admin, action="update", entity="Superviseur",
                    path="/admin/superviseurs/import-affectations",
                    description=f"Import des affectations superviseurs → tuteurs "
                                f"({'remplacement' if remplacer else 'ajout'}) : "
                                f"{len(r['affectes'])} superviseur(s) mis à jour.")
    return {"dry_run": dry_run, "remplacer": remplacer, "lignes": len(lignes), **r}


@router.get("/{superviseur_id}", response_model=UserResponse)
async def get_superviseur(superviseur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.get_managed(db, superviseur_id, current_user, _SUPERVISEUR_ROLES)


@router.patch("/{superviseur_id}", response_model=UserResponse)
async def update_superviseur(
    superviseur_id: uuid.UUID, body: UserUpdate, db: DB, current_user: AdminUser
) -> UserResponse:
    return await user_service.update_user(db, superviseur_id, body, current_user, _SUPERVISEUR_ROLES)


@router.delete("/{superviseur_id}")
async def delete_superviseur(
    superviseur_id: uuid.UUID, db: DB, current_user: AdminUser, definitif: bool = Query(False, description="Suppression physique, seulement sans historique (APP-15)")
) -> dict:
    return await user_service.delete_user(
        db, superviseur_id, current_user.id, current_user, _SUPERVISEUR_ROLES, definitif=definitif,
    )


@router.post("/{superviseur_id}/restaurer")
async def restore_superviseur(superviseur_id: uuid.UUID, db: DB, current_user: AdminUser) -> dict:
    return await user_service.restore_user(db, superviseur_id, current_user, _SUPERVISEUR_ROLES)


@router.post("/{superviseur_id}/toggle-status", response_model=UserResponse)
async def toggle_status(superviseur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, superviseur_id, current_user, _SUPERVISEUR_ROLES)


@router.post("/{superviseur_id}/assign-teachers", status_code=status.HTTP_204_NO_CONTENT)
async def assign_teachers(
    superviseur_id: uuid.UUID,
    body: dict,
    db: DB,
    current_user: AdminUser,
) -> Response:
    """
    Assigne une liste d'enseignants à un superviseur via le champ `classes`
    (stockage des IDs enseignants assignés — adapté au modèle existant).
    Retourne 204 No Content.
    """
    # On vérifie que le superviseur existe et que le compte est bien un superviseur
    await user_service.get_managed(db, superviseur_id, current_user, _SUPERVISEUR_ROLES)
    try:
        assigned_uuids = list(dict.fromkeys(uuid.UUID(str(i)) for i in body.get("assigned_teacher_ids", [])))
    except ValueError:
        raise HTTPException(status_code=422, detail="Identifiant d'enseignant invalide.")
    # APP-06 : seuls des comptes enseignants existants et non archivés.
    valides = set((await db.execute(
        select(User.id).where(
            User.id.in_(assigned_uuids), User.role == UserRole.enseignant, User.archived_at.is_(None),
        )
    )).scalars().all())
    inconnus = [str(i) for i in assigned_uuids if i not in valides]
    if inconnus:
        raise HTTPException(
            status_code=422,
            detail={"message": "Ces identifiants ne sont pas des enseignants actifs.", "ids": inconnus},
        )
    assigned_ids: list[str] = [str(i) for i in assigned_uuids]

    # Stocker les IDs assignés dans le champ `classes` du superviseur
    # (champ ARRAY(String) polyvalent en attendant une table dédiée)
    result = await db.execute(select(User).where(User.id == superviseur_id))
    sup = result.scalar_one_or_none()
    if sup:
        sup.classes = assigned_ids
        await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
