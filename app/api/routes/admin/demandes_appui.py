"""Routes admin — demandes d'accompagnement et circuit de traitement (lot L6, D9.3).

  GET   /admin/demandes-appui                 liste filtrée (statut, origine, responsable, retard…)
  GET   /admin/demandes-appui/export          CSV : une ligne par demande, avec dernier événement
  GET   /admin/demandes-appui/evenements/export   CSV : un événement par ligne (circuit complet)
  POST  /admin/demandes-appui                 création manuelle
  POST  /admin/demandes-appui/depuis-rapport  depuis une difficulté / un besoin d'appui (idempotent)
  GET   /admin/demandes-appui/{id}            détail + événements
  PATCH /admin/demandes-appui/{id}            étape : statut, responsable, échéance, commentaire
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.core.deps import DB, AdminUser
from app.core.export_utils import sanitize_cell
from app.core.pagination import Page, Pagination
from app.models.demande_appui import STATUTS_DEMANDE_OUVERTS, DemandeAppui, DemandeAppuiEvenement
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User
from app.schemas.visite import DemandeAction, DemandeDepuisRapportIn, DemandeIn, DemandeOut
from app.services import demande_appui_service as svc

router = APIRouter(prefix="/demandes-appui", tags=["Admin — Demandes d'appui"])


def _requete(statut, origine, responsable_id, beneficiaire_id, school_id, session_id, domaine, en_retard, sans_responsable):
    q = select(DemandeAppui)
    if statut == "ouvertes":
        q = q.where(DemandeAppui.statut.in_(STATUTS_DEMANDE_OUVERTS))
    elif statut and statut != "toutes":
        q = q.where(DemandeAppui.statut == statut)
    if origine:
        q = q.where(DemandeAppui.origine == origine)
    if responsable_id:
        q = q.where(DemandeAppui.responsable_id == responsable_id)
    if sans_responsable:
        q = q.where(DemandeAppui.responsable_id.is_(None))
    if beneficiaire_id:
        q = q.where(DemandeAppui.beneficiaire_id == beneficiaire_id)
    if school_id:
        q = q.where(DemandeAppui.school_id == school_id)
    if session_id:
        q = q.where(DemandeAppui.session_id == session_id)
    if domaine:
        q = q.where(DemandeAppui.domaine == domaine)
    if en_retard:
        q = q.where(DemandeAppui.echeance < date.today(), DemandeAppui.statut.in_(STATUTS_DEMANDE_OUVERTS))
    return q


@router.get("", response_model=Page[DemandeOut])
async def lister(
    db: DB, _: AdminUser, page: Pagination,
    statut: str = Query(default="ouvertes"), origine: Optional[str] = None,
    responsable_id: Optional[uuid.UUID] = None, beneficiaire_id: Optional[uuid.UUID] = None,
    school_id: Optional[uuid.UUID] = None, session_id: Optional[uuid.UUID] = None,
    domaine: Optional[str] = None, en_retard: bool = False, sans_responsable: bool = False,
) -> Page[DemandeOut]:
    q = _requete(statut, origine, responsable_id, beneficiaire_id, school_id, session_id, domaine, en_retard, sans_responsable)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(DemandeAppui.echeance.asc().nulls_last(), DemandeAppui.created_at.desc())
                             .offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=await svc.serialiser(db, list(rows)))


def _csv(nom: str, entetes: list[str], lignes) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(entetes)
    for l in lignes:
        w.writerow([sanitize_cell(c) if c is not None else "" for c in l])
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nom}_{date.today():%Y%m%d}.csv"'})


@router.get("/export")
async def exporter(
    db: DB, _: AdminUser,
    statut: str = Query(default="toutes"), origine: Optional[str] = None,
    responsable_id: Optional[uuid.UUID] = None, school_id: Optional[uuid.UUID] = None,
    session_id: Optional[uuid.UUID] = None, en_retard: bool = False,
) -> StreamingResponse:
    q = _requete(statut, origine, responsable_id, None, school_id, session_id, None, en_retard, False)
    items = await svc.serialiser(db, list((await db.execute(q.order_by(DemandeAppui.created_at))).scalars()))
    return _csv("demandes_appui", [
        "demande_id", "origine", "statut", "en_retard", "titre", "domaine", "libelle_source", "rapport_id", "visite_id",
        "beneficiaire_id", "beneficiaire", "ecole_id", "ecole", "session_id", "responsable_id", "responsable",
        "echeance", "creee_le", "cloturee_le",
    ], ([d.id, d.origine, d.statut, int(d.en_retard), d.titre, d.domaine, d.libelle_source, d.rapport_id, d.visite_id,
         d.beneficiaire_id, d.beneficiaire_nom, d.school_id, d.ecole, d.session_id, d.responsable_id, d.responsable_nom,
         d.echeance.isoformat() if d.echeance else None, d.created_at.isoformat(),
         d.cloturee_le.isoformat() if d.cloturee_le else None] for d in items))


@router.get("/evenements/export")
async def exporter_evenements(db: DB, _: AdminUser, session_id: Optional[uuid.UUID] = None) -> StreamingResponse:
    q = select(DemandeAppuiEvenement, DemandeAppui.origine, DemandeAppui.session_id).join(
        DemandeAppui, DemandeAppui.id == DemandeAppuiEvenement.demande_id)
    if session_id:
        q = q.where(DemandeAppui.session_id == session_id)
    rows = (await db.execute(q.order_by(DemandeAppuiEvenement.created_at))).all()
    noms = dict((await db.execute(select(User.id, User.name))).all())
    return _csv("demandes_appui_evenements", [
        "evenement_id", "demande_id", "origine", "session_id", "type", "ancien_statut", "nouveau_statut",
        "responsable_id", "responsable", "auteur_id", "auteur", "commentaire", "date",
    ], ([e.id, e.demande_id, origine, sid, e.type, e.ancien_statut, e.nouveau_statut, e.responsable_id,
         noms.get(e.responsable_id), e.auteur_id, noms.get(e.auteur_id), e.commentaire, e.created_at.isoformat()]
        for e, origine, sid in rows))


@router.post("", response_model=DemandeOut, status_code=201)
async def creer(body: DemandeIn, db: DB, user: AdminUser) -> DemandeOut:
    d = await svc.creer(db, user, origine="manuel", **body.model_dump())
    return (await svc.serialiser(db, [d], avec_evenements=True))[0]


@router.post("/depuis-rapport", response_model=DemandeOut)
async def depuis_rapport(body: DemandeDepuisRapportIn, db: DB, user: AdminUser) -> DemandeOut:
    r = (await db.execute(select(RapportJournalier).where(RapportJournalier.id == body.rapport_id))).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Rapport introuvable.")
    d, _ = await svc.depuis_rapport(db, user, r, body.type, body.libelle, body.responsable_id, body.echeance, body.description)
    return (await svc.serialiser(db, [d], avec_evenements=True))[0]


async def _charger(db, demande_id: uuid.UUID) -> DemandeAppui:
    d = (await db.execute(select(DemandeAppui).where(DemandeAppui.id == demande_id).with_for_update())).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Demande introuvable.")
    return d


@router.get("/{demande_id}", response_model=DemandeOut)
async def lire(demande_id: uuid.UUID, db: DB, _: AdminUser) -> DemandeOut:
    d = (await db.execute(select(DemandeAppui).where(DemandeAppui.id == demande_id))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Demande introuvable.")
    return (await svc.serialiser(db, [d], avec_evenements=True))[0]


@router.patch("/{demande_id}", response_model=DemandeOut)
async def agir(demande_id: uuid.UUID, body: DemandeAction, db: DB, user: AdminUser) -> DemandeOut:
    d = await svc.appliquer_action(db, await _charger(db, demande_id), user, body)
    return (await svc.serialiser(db, [d], avec_evenements=True))[0]
