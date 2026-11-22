"""App superviseur — suivi des tuteurs affectés (lot L7, retours terrain).

Toutes les routes sont limitées aux tuteurs affectés au superviseur
(`get_supervised_teacher_ids`) : un tuteur hors périmètre répond 404.

- RET03 : demandes de remplacement à valider (si ARED l'a décidé) ;
- RET05 : rapports des tuteurs consultables et retour écrit du superviseur ;
- RET13 : fraîcheur des données de chaque tuteur et messages reçus.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import DB, SuperviseurUser
from app.models.eleve_remplacement import EleveRemplacement
from app.models.rapport_journalier import RAPPORT_COMPTE, RapportJournalier
from app.models.remarque import Remarque
from app.models.terrain import ContactApp
from app.models.user import User
from app.schemas.eleve_remplacement import DecisionRemplacement, EleveRemplacementResponse
from app.schemas.rapport_journalier import RapportJournalierListItem
from app.services.audit_service import journaliser
from app.services.rapport_photos import LIST_COLUMNS
from app.services.supervisor_service import get_supervised_teacher_ids
from app.services.terrain_service import decider_remplacement

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur (suivi des tuteurs)"])


async def _perimetre(db, sup: User) -> set[uuid.UUID]:
    return set(await get_supervised_teacher_ids(db, sup))


# ── RET03 : remplacements ─────────────────────────────────────────────────────

@router.get("/remplacements", response_model=list[EleveRemplacementResponse])
async def remplacements_tuteurs(current_user: SuperviseurUser, db: DB,
                                statut: Optional[str] = Query(None)) -> list[EleveRemplacementResponse]:
    ids = await _perimetre(db, current_user)
    if not ids:
        return []
    stmt = select(EleveRemplacement).where(EleveRemplacement.teacher_id.in_(ids))
    if statut:
        stmt = stmt.where(EleveRemplacement.statut == statut)
    rows = (await db.execute(stmt.order_by(EleveRemplacement.created_at.desc()).limit(200))).scalars().all()
    noms = dict((await db.execute(select(User.id, User.name).where(User.id.in_({r.teacher_id for r in rows})))).all()) if rows else {}
    out = []
    for r in rows:
        o = EleveRemplacementResponse.model_validate(r)
        o.teacher_name = noms.get(r.teacher_id)
        out.append(o)
    return out


@router.post("/remplacements/{remplacement_id}/decision", response_model=EleveRemplacementResponse)
async def decider_remplacement_tuteur(remplacement_id: uuid.UUID, body: DecisionRemplacement,
                                      current_user: SuperviseurUser, db: DB) -> EleveRemplacementResponse:
    rempl = await db.get(EleveRemplacement, remplacement_id)
    if rempl is None or rempl.teacher_id not in await _perimetre(db, current_user):
        raise HTTPException(status_code=404, detail="Demande introuvable.")
    rempl = await decider_remplacement(db, rempl, body.decision, body.motif, current_user)
    return EleveRemplacementResponse.model_validate(rempl)


# ── RET05 : rapports des tuteurs et retour du superviseur ────────────────────

@router.get("/rapports-tuteurs", response_model=list[RapportJournalierListItem])
async def rapports_tuteurs(current_user: SuperviseurUser, db: DB,
                           teacher_id: Optional[uuid.UUID] = None,
                           date_debut: Optional[date] = None, date_fin: Optional[date] = None,
                           limit: int = Query(50, ge=1, le=200)) -> list[RapportJournalierListItem]:
    """Rapports journaliers des tuteurs affectés (sans photos), du plus récent au plus ancien."""
    ids = await _perimetre(db, current_user)
    if teacher_id is not None:
        if teacher_id not in ids:
            raise HTTPException(status_code=404, detail="Tuteur introuvable dans votre suivi.")
        ids = {teacher_id}
    if not ids:
        return []
    stmt = select(*LIST_COLUMNS).where(RapportJournalier.teacher_id.in_(ids))
    if date_debut:
        stmt = stmt.where(RapportJournalier.date_rapport >= date_debut)
    if date_fin:
        stmt = stmt.where(RapportJournalier.date_rapport <= date_fin)
    rows = (await db.execute(
        stmt.order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc()).limit(limit)
    )).mappings().all()
    return [RapportJournalierListItem(**r) for r in rows]


class RetourIn(BaseModel):
    texte: str = Field(..., min_length=3, max_length=2000)


@router.put("/rapports-tuteurs/{rapport_id}/retour", response_model=RapportJournalierListItem)
async def retour_rapport(rapport_id: uuid.UUID, body: RetourIn,
                         current_user: SuperviseurUser, db: DB) -> RapportJournalierListItem:
    rapport = await db.get(RapportJournalier, rapport_id)
    if rapport is None or rapport.teacher_id not in await _perimetre(db, current_user):
        raise HTTPException(status_code=404, detail="Rapport introuvable dans votre suivi.")
    texte = body.texte.strip()
    if len(texte) < 3:
        raise HTTPException(status_code=422, detail="Le retour doit contenir au moins 3 caractères.")
    rapport.retour_superviseur = texte
    rapport.retour_par_id = current_user.id
    rapport.retour_le = datetime.now(timezone.utc)
    journaliser(db, acteur=current_user, action="update", entity="Rapports journaliers",
                path=f"/api/v1/app/supervisor/rapports-tuteurs/{rapport_id}/retour",
                description=f"Retour du superviseur sur le rapport du {rapport.date_rapport.isoformat()}",
                cible_type="rapport_journalier", cible_id=rapport.id, method="PUT")
    await db.commit()
    row = (await db.execute(select(*LIST_COLUMNS).where(RapportJournalier.id == rapport_id))).mappings().one()
    return RapportJournalierListItem(**row)


# ── RET13 : fraîcheur des données des tuteurs ────────────────────────────────

class ActiviteTuteur(BaseModel):
    teacher_id: uuid.UUID
    nom: str
    derniere_sync_le: Optional[datetime] = None
    version_app: Optional[str] = None
    dernier_rapport: Optional[date] = None
    rapports_7_jours: int = 0
    # « jamais synchronisé », « à jour », « données de plus de N jours » : la
    # fraîcheur des données, pas un jugement sur le travail du tuteur.
    fraicheur: str


@router.get("/tuteurs-activite", response_model=list[ActiviteTuteur])
async def tuteurs_activite(current_user: SuperviseurUser, db: DB) -> list[ActiviteTuteur]:
    ids = await _perimetre(db, current_user)
    if not ids:
        return []
    tuteurs = (await db.execute(select(User.id, User.name).where(User.id.in_(ids)).order_by(User.name))).all()
    contacts = {c.user_id: c for c in (await db.execute(select(ContactApp).where(ContactApp.user_id.in_(ids)))).scalars()}
    derniers = dict((await db.execute(
        select(RapportJournalier.teacher_id, func.max(RapportJournalier.date_rapport))
        .where(RapportJournalier.teacher_id.in_(ids), RAPPORT_COMPTE).group_by(RapportJournalier.teacher_id)
    )).all())
    il_y_a_7 = date.today() - timedelta(days=7)
    recents = dict((await db.execute(
        select(RapportJournalier.teacher_id, func.count())
        .where(RapportJournalier.teacher_id.in_(ids), RAPPORT_COMPTE, RapportJournalier.date_rapport >= il_y_a_7)
        .group_by(RapportJournalier.teacher_id)
    )).all())
    maintenant = datetime.now(timezone.utc)
    out = []
    for tid, nom in tuteurs:
        c = contacts.get(tid)
        if c is None:
            fraicheur = "jamais synchronisé"
        else:
            jours = (maintenant - c.derniere_sync_le).days
            fraicheur = "à jour" if jours < 1 else f"données de plus de {jours} jour{'s' if jours > 1 else ''}"
        out.append(ActiviteTuteur(
            teacher_id=tid, nom=nom or "Tuteur", derniere_sync_le=c.derniere_sync_le if c else None,
            version_app=c.version_app if c else None, dernier_rapport=derniers.get(tid),
            rapports_7_jours=int(recents.get(tid, 0)), fraicheur=fraicheur,
        ))
    return out


# ── RET13 : messages adressés au superviseur ─────────────────────────────────

class MessageOut(BaseModel):
    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    user_name: str
    categorie: str
    message: str
    statut: str
    contexte_type: Optional[str] = None
    contexte_id: Optional[str] = None
    reponse_admin: Optional[str] = None
    reponse_admin_at: Optional[datetime] = None
    reponse_par_nom: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/messages", response_model=list[MessageOut])
async def messages_recus(current_user: SuperviseurUser, db: DB) -> list[MessageOut]:
    ids = await _perimetre(db, current_user)
    if not ids:
        return []
    rows = (await db.execute(
        select(Remarque).where(Remarque.destinataire == "superviseur", Remarque.user_id.in_(ids))
        .order_by(Remarque.created_at.desc()).limit(200)
    )).scalars().all()
    return [MessageOut.model_validate(r) for r in rows]


class ReponseIn(BaseModel):
    texte: str = Field(..., min_length=2, max_length=2000)


@router.post("/messages/{message_id}/reponse", response_model=MessageOut)
async def repondre_message(message_id: uuid.UUID, body: ReponseIn,
                           current_user: SuperviseurUser, db: DB) -> MessageOut:
    r = await db.get(Remarque, message_id)
    if r is None or r.destinataire != "superviseur" or r.user_id not in await _perimetre(db, current_user):
        raise HTTPException(status_code=404, detail="Message introuvable.")
    r.reponse_admin = body.texte.strip()
    r.reponse_admin_at = datetime.now(timezone.utc)
    r.reponse_par_nom = current_user.name
    r.statut = "traite"
    await db.commit()
    await db.refresh(r)
    return MessageOut.model_validate(r)
