"""Routes admin — protocole de recherche et accès au minuteur (lot L6, APP-33).

  GET  /admin/recherche/matrice?session_id=        tuteurs × groupe × accès stocké / attendu / effectif
  POST /admin/recherche/aligner                    aligne les accès sur le protocole (aperçu puis application)
  GET  /admin/recherche/historique?user_id=        historique immuable des assignations
  POST /admin/recherche/assignation                assignation ou exception motivée pour un tuteur
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DB, AdminUser
from app.models.recherche import AssignationRecherche
from app.models.user import User, UserGroupe, UserRole
from app.services import campagne_service as camp
from app.services import recherche_service as rs
from app.services.audit_service import journaliser

router = APIRouter(prefix="/recherche", tags=["Admin — Protocole de recherche"])


class AlignementIn(BaseModel):
    session_id: Optional[uuid.UUID] = None
    motif: str = Field(min_length=3, max_length=1000)
    dry_run: bool = True


class AssignationIn(BaseModel):
    user_id: uuid.UUID
    groupe_recherche: Optional[UserGroupe] = None
    app_access: str = Field(pattern="^(full|timer_only|no_timer)$")
    motif: str = Field(min_length=3, max_length=1000)


async def _session(db, session_id):
    s = await camp.session_ou_active(db, session_id)
    if s is None:
        raise HTTPException(404, "Aucune campagne active ni choisie.")
    return s


@router.get("/matrice")
async def matrice(db: DB, _: AdminUser, session_id: Optional[uuid.UUID] = None) -> dict:
    return await rs.matrice(db, await _session(db, session_id))


@router.post("/aligner")
async def aligner(body: AlignementIn, db: DB, user: AdminUser) -> dict:
    return await rs.aligner(db, await _session(db, body.session_id), user, body.motif, body.dry_run)


@router.get("/historique")
async def historique(db: DB, _: AdminUser, user_id: Optional[uuid.UUID] = None, limit: int = 500) -> list[dict]:
    q = select(AssignationRecherche).order_by(AssignationRecherche.effective_le.desc()).limit(min(limit, 5000))
    if user_id:
        q = q.where(AssignationRecherche.user_id == user_id)
    rows = (await db.execute(q)).scalars().all()
    noms = dict((await db.execute(select(User.id, User.name).where(
        User.id.in_({i for r in rows for i in (r.user_id, r.auteur_id) if i})))).all()) if rows else {}
    return [{"id": r.id, "user_id": r.user_id, "nom": noms.get(r.user_id), "session_id": r.session_id,
             "groupe_recherche": r.groupe_recherche, "app_access": r.app_access, "exception": r.exception,
             "motif": r.motif, "auteur": noms.get(r.auteur_id) or "système", "effective_le": r.effective_le} for r in rows]


@router.post("/assignation")
async def assigner(body: AssignationIn, db: DB, user: AdminUser) -> dict:
    u = (await db.execute(select(User).where(User.id == body.user_id).with_for_update())).scalar_one_or_none()
    if u is None or u.role != UserRole.enseignant:
        raise HTTPException(404, "Tuteur introuvable.")
    u.groupe_recherche = body.groupe_recherche
    u.app_access = body.app_access
    session = await camp.session_ou_active(db, None)
    from app.services.progression_service import active_session_for
    session = await active_session_for(db, u) or session
    attendu = rs.acces_matrice(body.groupe_recherche.value if body.groupe_recherche else None, body.app_access)
    exception = bool(session and session.protocole_minuteur and attendu is not None and attendu != body.app_access)
    await rs.tracer(db, u, user, body.motif, session.id if session else None, exception)
    journaliser(db, acteur=user, action="assignation", entity="Protocole de recherche",
                description=f"{u.name} : groupe {body.groupe_recherche.value if body.groupe_recherche else '—'}, "
                            f"accès {body.app_access}{' (exception)' if exception else ''}",
                cible_type="user", cible_id=u.id, motif=body.motif, path="/recherche/assignation")
    await db.flush()
    return {"user_id": u.id, "exception": exception, **(await rs.acces_effectif(db, u, session))}
