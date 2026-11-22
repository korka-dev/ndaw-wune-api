"""App mobile superviseur — visites pédagogiques et demandes d'appui (lot L6, D9.3).

  GET   /app/supervisor/grilles-visite          grilles publiées utilisables (avec items)
  GET   /app/supervisor/visites                 ses visites (paginées)
  POST  /app/supervisor/visites                 envoyer une visite (idempotent, hors ligne)
  GET   /app/supervisor/visites/{id}            détail (réponses, demandes créées)
  GET   /app/supervisor/demandes-appui          demandes de son périmètre
  PATCH /app/supervisor/demandes-appui/{id}     faire avancer une demande de son périmètre
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import DB, SuperviseurUser
from app.core.pagination import Page, Pagination
from app.models.demande_appui import STATUTS_DEMANDE_OUVERTS, DemandeAppui
from app.models.visite import Visite
from app.schemas.visite import DemandeAction, DemandeOut, GrilleOut, VisiteIn, VisiteOut
from app.services import demande_appui_service, visite_service
from app.services.progression_service import active_session_for

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur (visites)"])


@router.get("/grilles-visite", response_model=list[GrilleOut])
async def grilles(current_user: SuperviseurUser, db: DB) -> list[GrilleOut]:
    session = await active_session_for(db, current_user)
    return [await visite_service.grille_out(db, g)
            for g in await visite_service.grilles_utilisables(db, session.id if session else None)]


@router.get("/visites", response_model=Page[VisiteOut])
async def mes_visites(current_user: SuperviseurUser, db: DB, page: Pagination) -> Page[VisiteOut]:
    q = select(Visite).where(Visite.superviseur_id == current_user.id)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Visite.date_visite.desc(), Visite.created_at.desc())
                             .offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=await visite_service.serialiser(db, list(rows)))


@router.post("/visites", response_model=VisiteOut, status_code=status.HTTP_201_CREATED)
async def envoyer_visite(body: VisiteIn, current_user: SuperviseurUser, db: DB) -> VisiteOut:
    v = await visite_service.creer(db, current_user, body)
    return (await visite_service.serialiser(db, [v], detail=True))[0]


@router.get("/visites/{visite_id}", response_model=VisiteOut)
async def ma_visite(visite_id: uuid.UUID, current_user: SuperviseurUser, db: DB) -> VisiteOut:
    v = (await db.execute(select(Visite).where(
        Visite.id == visite_id, Visite.superviseur_id == current_user.id))).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "Visite introuvable.")
    return (await visite_service.serialiser(db, [v], detail=True))[0]


@router.get("/demandes-appui", response_model=list[DemandeOut])
async def demandes(
    current_user: SuperviseurUser, db: DB,
    statut: str = Query(default="ouvertes"),
) -> list[DemandeOut]:
    q = select(DemandeAppui).where(await demande_appui_service.condition_superviseur(db, current_user))
    if statut == "ouvertes":
        q = q.where(DemandeAppui.statut.in_(STATUTS_DEMANDE_OUVERTS))
    elif statut != "toutes":
        q = q.where(DemandeAppui.statut == statut)
    rows = (await db.execute(q.order_by(DemandeAppui.echeance.asc().nulls_last(), DemandeAppui.created_at.desc())
                             .limit(500))).scalars().all()
    return await demande_appui_service.serialiser(db, list(rows), avec_evenements=True)


@router.patch("/demandes-appui/{demande_id}", response_model=DemandeOut)
async def agir(demande_id: uuid.UUID, body: DemandeAction, current_user: SuperviseurUser, db: DB) -> DemandeOut:
    d = (await db.execute(select(DemandeAppui).where(
        DemandeAppui.id == demande_id, await demande_appui_service.condition_superviseur(db, current_user),
    ).with_for_update())).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Demande introuvable dans votre périmètre.")
    # Un superviseur ne peut s'attribuer que lui-même une demande.
    if body.responsable_id and body.responsable_id != current_user.id:
        raise HTTPException(403, "Un superviseur ne peut affecter une demande qu'à lui-même.")
    if body.retirer_responsable:
        raise HTTPException(403, "Le retrait d'un responsable est réservé à l'administration.")
    d = await demande_appui_service.appliquer_action(db, d, current_user, body)
    return (await demande_appui_service.serialiser(db, [d], avec_evenements=True))[0]
