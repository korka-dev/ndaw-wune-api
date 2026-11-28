"""Endpoints Admin — Inspecteurs (lot L8).

Un inspecteur se connecte à l'application mobile (par son téléphone) et
consulte, en lecture seule, les écoles de son IEF et les rapports de leurs
tuteurs et superviseurs. L'admin crée le compte et choisit l'IEF parmi celles
portées par les écoles (`schools.region`).
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.pagination import Page, Pagination
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services import user_service
from app.services.archivage import non_archive
from app.core.upload_utils import read_upload_capped
from app.services import points_focaux_service
from app.services.audit_service import journaliser
from app.services.inspecteur_service import ief_canonique, liste_iefs

router = APIRouter(prefix="/inspecteurs", tags=["Admin — Inspecteurs"])

_ROLES = (UserRole.inspecteur,)


@router.get("", response_model=Page[UserResponse])
async def list_inspecteurs(db: DB, _: AdminUser, page: Pagination,
                           archives: bool = Query(False, description="Inclure les éléments archivés")) -> Page[UserResponse]:
    base = (
        select(User).where(User.role == UserRole.inspecteur, non_archive(User, archives))
        .options(selectinload(User.school)).order_by(User.name)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


@router.get("/iefs")
async def iefs(db: DB, _: AdminUser) -> list[dict]:
    """IEF disponibles (libellés des écoles) et nombre d'écoles actives de chacune."""
    return await liste_iefs(db)


class InspecteurCreate(UserCreate):
    # Le rôle est imposé par la route : le formulaire admin ne l'envoie pas
    # (sinon 422 « Field required » à chaque création).
    role: UserRole = UserRole.inspecteur


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_inspecteur(body: InspecteurCreate, db: DB, _: AdminUser) -> UserResponse:
    """Crée un inspecteur ; mot de passe temporaire à changer à la première connexion."""
    if not body.phone:
        raise HTTPException(status_code=422, detail="Le téléphone est obligatoire : l'inspecteur se connecte à l'application avec.")
    ief = await ief_canonique(db, body.ief)
    body = body.model_copy(update={
        "ief": ief, "school_id": None, "niveau": None, "classes": None,
        "groupe_recherche": None, "app_access": "full",
    })
    return await user_service.create_user(db, body, force_role=UserRole.inspecteur)


@router.post("/import-points-focaux")
async def importer_points_focaux(
    db: DB,
    admin: AdminUser,
    file: UploadFile = File(...),
    dry_run: bool = Query(True, description="Aperçu : rien n'est enregistré."),
    rattachements: str = Form("{}", description='JSON {"PF-007": "IEF …"} : IEF choisie pour un point focal sans IEF.'),
    exclus: str = Form("[]", description='JSON ["PF-010"] : points focaux laissés de côté.'),
) -> dict:
    """Lot L10 : crée les comptes inspecteurs depuis l'onglet « Points_focaux »
    de la base de l'année (le même fichier que l'import global)."""
    try:
        choix = json.loads(rattachements or "{}")
        ecartes = json.loads(exclus or "[]")
        assert isinstance(choix, dict) and isinstance(ecartes, list)
    except (ValueError, AssertionError):
        raise HTTPException(status_code=422, detail="rattachements / exclus : JSON invalide.")
    points = points_focaux_service.lire(await read_upload_capped(file))
    r = await points_focaux_service.traiter(db, points, {str(k): str(v) for k, v in choix.items() if v},
                                            {str(e) for e in ecartes})
    if dry_run:
        await db.rollback()
    elif r["crees"] or r["mis_a_jour"]:
        journaliser(db, acteur=admin, action="create", entity="Inspecteur", path="/admin/inspecteurs/import-points-focaux",
                    description=f"Import des points focaux : {len(r['crees'])} compte(s) inspecteur créé(s), "
                                f"{len(r['mis_a_jour'])} mis à jour.")
    return {"dry_run": dry_run, "lignes": len(points), **r}


@router.get("/{inspecteur_id}", response_model=UserResponse)
async def get_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.get_managed(db, inspecteur_id, current_user, _ROLES)


@router.patch("/{inspecteur_id}", response_model=UserResponse)
async def update_inspecteur(inspecteur_id: uuid.UUID, body: UserUpdate, db: DB, current_user: AdminUser) -> UserResponse:
    champs = body.model_dump(exclude_unset=True)
    interdits = {"school_id", "niveau", "classes", "groupe_recherche", "app_access", "motif_protocole"} & champs.keys()
    if interdits:
        raise HTTPException(status_code=422, detail=f"Champs sans objet pour un inspecteur : {', '.join(sorted(interdits))}.")
    if "ief" in champs:
        body = body.model_copy(update={"ief": await ief_canonique(db, body.ief)})
    # Email et fonction s'effacent en envoyant null ou "" (le service commun
    # ignore les valeurs nulles) ; le téléphone, identifiant de connexion, reste obligatoire.
    if "phone" in champs and not champs["phone"]:
        raise HTTPException(status_code=422, detail="Le téléphone est obligatoire : l'inspecteur se connecte à l'application avec.")
    a_vider = [k for k in ("email", "title") if k in champs and not (champs[k] or "").strip()]
    user = await user_service.update_user(db, inspecteur_id, body, current_user, _ROLES)
    if a_vider:
        for k in a_vider:
            setattr(user, k, None)
        await db.flush()
        await db.refresh(user, attribute_names=["school"])
    return user


@router.delete("/{inspecteur_id}")
async def delete_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser,
                            definitif: bool = Query(False, description="Suppression physique, seulement sans historique (APP-15)")) -> dict:
    return await user_service.delete_user(db, inspecteur_id, current_user.id, current_user, _ROLES, definitif=definitif)


@router.post("/{inspecteur_id}/restaurer")
async def restore_inspecteur(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> dict:
    return await user_service.restore_user(db, inspecteur_id, current_user, _ROLES)


@router.post("/{inspecteur_id}/toggle-status", response_model=UserResponse)
async def toggle_status(inspecteur_id: uuid.UUID, db: DB, current_user: AdminUser) -> UserResponse:
    return await user_service.toggle_status(db, inspecteur_id, current_user, _ROLES)
