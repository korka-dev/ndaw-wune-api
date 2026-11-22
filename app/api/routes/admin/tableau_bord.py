"""Routes admin — vues de tableau de bord (lot L6, Partie II D8).

  GET /admin/tableau-de-bord/vue-ensemble
  GET /admin/tableau-de-bord/soumissions
  GET /admin/tableau-de-bord/relances            (+ format=csv)
  GET /admin/tableau-de-bord/assiduite           (+ format=csv)
  GET /admin/tableau-de-bord/supervision
  GET /admin/tableau-de-bord/appui
  GET /admin/presences-eleves                    lignes d'appel (détail d'une alerte)
  PUT /admin/presences-eleves                    correction motivée (absence excusée…)

Les listes nominatives (relances, assiduité) sont réservées à l'administration
et chaque téléchargement CSV est inscrit à l'historique.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import DB, AdminUser
from app.core.export_utils import sanitize_cell
from app.core.pagination import Page, Pagination
from app.models.eleve import Eleve
from app.models.presence_eleve import PresenceEleve
from app.services import tableau_bord as tb
from app.services.audit_service import journaliser
from app.services.presences_service import enregistrer_appel

router = APIRouter(tags=["Admin — Tableau de bord (D8)"])


def filtres(
    session_id: Optional[uuid.UUID] = None, date_debut: Optional[date] = None, date_fin: Optional[date] = None,
    region: Optional[str] = None, commune: Optional[str] = None, school_id: Optional[uuid.UUID] = None,
    teacher_id: Optional[uuid.UUID] = None,
) -> tb.Filtres:
    return tb.Filtres(session_id, date_debut, date_fin, region, commune, school_id, teacher_id)


FiltresDep = Annotated[tb.Filtres, Depends(filtres)]


def _csv(nom: str, lignes: list[dict], colonnes: list[str]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(colonnes)
    for l in lignes:
        w.writerow([sanitize_cell(", ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v))
                    for v in (l.get(c) for c in colonnes)])
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nom}_{date.today():%Y%m%d}.csv"'})


@router.get("/tableau-de-bord/vue-ensemble")
async def vue_ensemble(f: FiltresDep, db: DB, _: AdminUser) -> dict:
    return await tb.vue_ensemble(db, f)


@router.get("/tableau-de-bord/soumissions")
async def soumissions(f: FiltresDep, db: DB, _: AdminUser) -> dict:
    return await tb.soumissions(db, f)


@router.get("/tableau-de-bord/relances")
async def relances(f: FiltresDep, db: DB, user: AdminUser, format: str = Query("json", pattern="^(json|csv)$")):
    vue = await tb.relances(db, f)
    if format == "json" or vue["statut"] != "calcule":
        return vue
    journaliser(db, acteur=user, action="export", entity="Tableau de bord", method="GET",
                path="/admin/tableau-de-bord/relances", cible_type="export",
                description=f"Liste de relance nominative : {len(vue['lignes'])} ligne(s)")
    return _csv("relances", vue["lignes"], ["acteur_id", "acteur", "role", "objet", "ecole", "region", "commune",
                                            "attendu", "recu", "manquant", "en_retard", "taux", "derniere_soumission"])


@router.get("/tableau-de-bord/assiduite")
async def assiduite(f: FiltresDep, db: DB, user: AdminUser, format: str = Query("json", pattern="^(json|csv)$")):
    vue = await tb.assiduite(db, f, limite=10**9 if format == "csv" else 2000)
    if format == "json" or vue["statut"] != "calcule":
        return vue
    journaliser(db, acteur=user, action="export", entity="Tableau de bord", method="GET",
                path="/admin/tableau-de-bord/assiduite", cible_type="export",
                description=f"Assiduité individuelle nominative : {len(vue['lignes'])} élève(s)")
    return _csv("assiduite", vue["lignes"], ["eleve_id", "nom", "classe", "ecole", "jours_attendus", "jours_renseignes",
                                             "jours_non_renseignes", "absences", "absences_excusees", "taux_absence",
                                             "max_absences_consecutives", "alertes"])


@router.get("/tableau-de-bord/supervision")
async def supervision(f: FiltresDep, db: DB, _: AdminUser) -> dict:
    return await tb.supervision(db, f)


@router.get("/tableau-de-bord/appui")
async def appui(f: FiltresDep, db: DB, _: AdminUser) -> dict:
    return await tb.appui(db, f)


# ── Lignes d'appel ────────────────────────────────────────────────────────────

class PresenceOut(BaseModel):
    id: uuid.UUID
    eleve_id: uuid.UUID
    eleve: str
    classe: str
    date_jour: date
    statut: str
    source: str
    motif: Optional[str]
    rapport_id: Optional[uuid.UUID]


class CorrectionPresence(BaseModel):
    eleve_id: uuid.UUID
    statut: str = Field(pattern="^(present|absent|excuse)$")
    motif: Optional[str] = Field(default=None, max_length=255)


class CorrectionAppel(BaseModel):
    date_jour: date
    session_id: Optional[uuid.UUID] = None
    lignes: list[CorrectionPresence] = Field(min_length=1, max_length=500)
    motif: str = Field(min_length=3, max_length=500)


@router.get("/presences-eleves", response_model=Page[PresenceOut])
async def lister_presences(
    db: DB, _: AdminUser, page: Pagination,
    eleve_id: Optional[uuid.UUID] = None, session_id: Optional[uuid.UUID] = None,
    date_debut: Optional[date] = None, date_fin: Optional[date] = None, statut: Optional[str] = None,
) -> Page[PresenceOut]:
    q = select(PresenceEleve, Eleve).join(Eleve, Eleve.id == PresenceEleve.eleve_id)
    if eleve_id:
        q = q.where(PresenceEleve.eleve_id == eleve_id)
    if session_id:
        q = q.where(PresenceEleve.session_id == session_id)
    if date_debut:
        q = q.where(PresenceEleve.date_jour >= date_debut)
    if date_fin:
        q = q.where(PresenceEleve.date_jour <= date_fin)
    if statut:
        q = q.where(PresenceEleve.statut == statut)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(PresenceEleve.date_jour.desc()).offset(page.skip).limit(page.limit))).all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=[PresenceOut(
        id=p.id, eleve_id=p.eleve_id, eleve=f"{e.nom} {e.prenom or ''}".strip(), classe=e.classe,
        date_jour=p.date_jour, statut=p.statut, source=p.source, motif=p.motif, rapport_id=p.rapport_id,
    ) for p, e in rows])


@router.put("/presences-eleves")
async def corriger_presences(body: CorrectionAppel, db: DB, user: AdminUser) -> dict:
    n = await enregistrer_appel(
        db, auteur=user, date_jour=body.date_jour, session_id=body.session_id, perimetre=None,
        lignes=[{"eleve_id": l.eleve_id, "statut": l.statut, "motif": l.motif or body.motif} for l in body.lignes],
    )
    journaliser(db, acteur=user, action="correction", entity="Présences élèves", method="PUT",
                path="/admin/presences-eleves", cible_type="presences", motif=body.motif,
                description=f"Correction de {n} présence(s) du {body.date_jour:%d/%m/%Y}")
    return {"corrigees": n}
