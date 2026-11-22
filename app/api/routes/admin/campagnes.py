"""Routes admin — paramètres de campagne (lot L6, D2.2, D8, Annexe D).

  GET   /admin/campagnes/{id}/parametres              calendrier, obligations et seuils effectifs
  POST  /admin/campagnes/{id}/calendrier/generer      génère les jours attendus (aperçu ou application)
  PATCH /admin/campagnes/{id}/calendrier/{date}       exception sur un jour (exclu / rétabli, motif)
  PUT   /admin/campagnes/{id}/obligations             obligations de soumission (version incrémentée à chaque changement)
  PUT   /admin/campagnes/{id}/seuils                  seuils d'alerte propres à la campagne
  PUT   /admin/seuils-alerte                          seuils globaux (toutes campagnes)
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.core.deps import DB, AdminUser
from app.models.campagne import (
    OBJETS_OBLIGATION, PERIODICITES, ROLES_OBLIGATION, CampagneJour, CampagneObligation, SeuilAlerte,
)
from app.services import campagne_service as camp
from app.services.audit_service import journaliser

router = APIRouter(tags=["Admin — Paramètres de campagne"])


class ExceptionJour(BaseModel):
    date: date
    motif: str = Field(min_length=2, max_length=255)


class GenerationCalendrier(BaseModel):
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    jours_semaine: list[int] = Field(min_length=1, max_length=7)  # 0 = lundi … 6 = dimanche
    exceptions: list[ExceptionJour] = Field(default_factory=list, max_length=366)
    remplacer: bool = False
    dry_run: bool = True

    @model_validator(mode="after")
    def _ok(self):
        if any(j < 0 or j > 6 for j in self.jours_semaine):
            raise ValueError("jours_semaine : valeurs de 0 (lundi) à 6 (dimanche).")
        return self


class JourPatch(BaseModel):
    attendu: bool
    motif: Optional[str] = Field(default=None, max_length=255)


class ObligationIn(BaseModel):
    role: str
    objet: str
    periodicite: str
    quantite: int = Field(ge=1, le=20)
    delai_jours: int = Field(ge=0, le=60)
    active: bool = True
    commentaire: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _ok(self):
        if self.role not in ROLES_OBLIGATION or self.objet not in OBJETS_OBLIGATION or self.periodicite not in PERIODICITES:
            raise ValueError("role, objet ou périodicité inconnu.")
        if self.objet == "pointage" and self.role != "superviseur":
            raise ValueError("Le pointage est une obligation de superviseur.")
        return self


class SeuilIn(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    valeur: float = Field(ge=0, le=10000)
    regle: Optional[str] = Field(default=None, max_length=2000)


async def _session(db, session_id):
    s = await camp.session_ou_active(db, session_id)
    return s


async def _parametres(db, s) -> dict:
    jours = (await db.execute(select(CampagneJour).where(CampagneJour.session_id == s.id)
                              .order_by(CampagneJour.date_jour))).scalars().all()
    obls = (await db.execute(select(CampagneObligation).where(CampagneObligation.session_id == s.id)
                             .order_by(CampagneObligation.role, CampagneObligation.objet))).scalars().all()
    return {
        "campagne": {"id": s.id, "nom": s.name, "date_debut": s.date_debut, "date_fin": s.date_fin,
                     "protocole_minuteur": s.protocole_minuteur},
        "calendrier": [{"date": j.date_jour, "attendu": j.attendu, "motif": j.motif} for j in jours],
        "nb_jours_attendus": sum(1 for j in jours if j.attendu),
        "obligations": [{"id": o.id, "role": o.role, "objet": o.objet, "periodicite": o.periodicite,
                         "quantite": o.quantite, "delai_jours": o.delai_jours, "active": o.active,
                         "version": o.version, "commentaire": o.commentaire, "modifiee_le": o.updated_at} for o in obls],
        "seuils": list((await camp.seuils(db, s.id)).values()),
    }


@router.get("/campagnes/{session_id}/parametres")
async def parametres(session_id: uuid.UUID, db: DB, _: AdminUser) -> dict:
    return await _parametres(db, await _session(db, session_id))


@router.post("/campagnes/{session_id}/calendrier/generer")
async def generer(session_id: uuid.UUID, body: GenerationCalendrier, db: DB, user: AdminUser) -> dict:
    s = await _session(db, session_id)
    debut = body.date_debut or s.date_debut
    fin = body.date_fin or s.date_fin
    if fin < debut:
        raise HTTPException(422, "date_fin antérieure à date_debut.")
    if debut < s.date_debut or fin > s.date_fin:
        raise HTTPException(422, "La période doit rester dans les dates de la campagne.")
    exc = {e.date: e.motif for e in body.exceptions}
    dates = camp.generer_dates(debut, fin, set(body.jours_semaine), exc)
    existants = set((await db.execute(select(CampagneJour.date_jour).where(
        CampagneJour.session_id == s.id, CampagneJour.date_jour.between(debut, fin)))).scalars().all())
    apercu = {
        "periode": {"debut": debut, "fin": fin}, "jours_generes": len(dates),
        "jours_attendus": sum(1 for _, a, _ in dates if a), "exceptions": sum(1 for _, a, _ in dates if not a),
        "deja_presents": len(existants & {d for d, _, _ in dates}),
        "supprimes_si_remplacement": len(existants - {d for d, _, _ in dates}) if body.remplacer else 0,
        "applique": False,
    }
    if body.dry_run:
        return apercu
    if body.remplacer:
        await db.execute(delete(CampagneJour).where(CampagneJour.session_id == s.id, CampagneJour.date_jour.between(debut, fin)))
    for d, attendu, motif in dates:
        stmt = insert(CampagneJour).values(id=uuid.uuid4(), session_id=s.id, date_jour=d, attendu=attendu, motif=motif)
        await db.execute(stmt.on_conflict_do_update(
            constraint="uq_campagne_jours_session_date", set_={"attendu": attendu, "motif": motif}))
    journaliser(db, acteur=user, action="parametrage", entity="Calendrier de campagne",
                description=f"Calendrier {s.name} : {apercu['jours_attendus']} jour(s) attendu(s), "
                            f"{apercu['exceptions']} exception(s), du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}",
                cible_type="session", cible_id=s.id, path=f"/campagnes/{s.id}/calendrier/generer")
    return {**apercu, "applique": True}


@router.patch("/campagnes/{session_id}/calendrier/{jour}")
async def modifier_jour(session_id: uuid.UUID, jour: date, body: JourPatch, db: DB, user: AdminUser) -> dict:
    s = await _session(db, session_id)
    if not (s.date_debut <= jour <= s.date_fin):
        raise HTTPException(422, "Date hors campagne.")
    if not body.attendu and not (body.motif or "").strip():
        raise HTTPException(422, "Un motif est requis pour exclure un jour.")
    stmt = insert(CampagneJour).values(id=uuid.uuid4(), session_id=s.id, date_jour=jour, attendu=body.attendu, motif=body.motif)
    await db.execute(stmt.on_conflict_do_update(constraint="uq_campagne_jours_session_date",
                                                set_={"attendu": body.attendu, "motif": body.motif}))
    journaliser(db, acteur=user, action="parametrage", entity="Calendrier de campagne",
                description=f"{jour:%d/%m/%Y} {'attendu' if body.attendu else 'exclu'}",
                cible_type="session", cible_id=s.id, motif=body.motif, method="PATCH",
                path=f"/campagnes/{s.id}/calendrier/{jour}")
    return {"date": jour, "attendu": body.attendu, "motif": body.motif}


@router.put("/campagnes/{session_id}/obligations")
async def definir_obligations(session_id: uuid.UUID, body: list[ObligationIn], db: DB, user: AdminUser) -> dict:
    s = await _session(db, session_id)
    cles = [(o.role, o.objet) for o in body]
    if len(cles) != len(set(cles)):
        raise HTTPException(422, "Une seule obligation par rôle et par objet.")
    existantes = {(o.role, o.objet): o for o in (await db.execute(
        select(CampagneObligation).where(CampagneObligation.session_id == s.id))).scalars()}
    changements = []
    for o in body:
        ex = existantes.get((o.role, o.objet))
        valeurs = o.model_dump(exclude={"role", "objet"})
        if ex is None:
            db.add(CampagneObligation(session_id=s.id, role=o.role, objet=o.objet, **valeurs))
            changements.append(f"+{o.role}/{o.objet}")
        elif any(getattr(ex, k) != v for k, v in valeurs.items()):
            for k, v in valeurs.items():
                setattr(ex, k, v)
            ex.version += 1
            changements.append(f"~{o.role}/{o.objet} v{ex.version}")
    for cle, ex in existantes.items():
        if cle not in cles and ex.active:
            ex.active = False
            ex.version += 1
            changements.append(f"-{cle[0]}/{cle[1]}")
    if changements:
        journaliser(db, acteur=user, action="parametrage", entity="Obligations de campagne",
                    description=f"Obligations {s.name} : {', '.join(changements)}", cible_type="session",
                    cible_id=s.id, method="PUT", path=f"/campagnes/{s.id}/obligations")
    await db.flush()
    return await _parametres(db, s)


async def _ecrire_seuils(db, user, session_id: Optional[uuid.UUID], body: list[SeuilIn], libelle: str) -> None:
    await db.execute(delete(SeuilAlerte).where(SeuilAlerte.session_id.is_not_distinct_from(session_id)))
    for x in body:
        db.add(SeuilAlerte(session_id=session_id, code=x.code, valeur=x.valeur, regle=x.regle))
    journaliser(db, acteur=user, action="parametrage", entity="Seuils d'alerte",
                description=f"Seuils {libelle} : " + ", ".join(f"{x.code}={x.valeur:g}" for x in body),
                cible_type="session" if session_id else None, cible_id=session_id, method="PUT",
                path="/seuils-alerte")
    await db.flush()


@router.put("/campagnes/{session_id}/seuils")
async def seuils_campagne(session_id: uuid.UUID, body: list[SeuilIn], db: DB, user: AdminUser) -> dict:
    s = await _session(db, session_id)
    await _ecrire_seuils(db, user, s.id, body, s.name)
    return await _parametres(db, s)


@router.get("/seuils-alerte")
async def seuils_globaux(db: DB, _: AdminUser) -> list[dict]:
    return list((await camp.seuils(db, None)).values())


@router.put("/seuils-alerte")
async def definir_seuils_globaux(body: list[SeuilIn], db: DB, user: AdminUser) -> list[dict]:
    await _ecrire_seuils(db, user, None, body, "globaux")
    return list((await camp.seuils(db, None)).values())
