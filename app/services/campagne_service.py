"""Paramètres de campagne : calendrier attendu, obligations, seuils (lot L6).

Les seuils ont une valeur PROPOSÉE (rapport V2, D8) utilisée tant qu'ARED n'en
a pas fixé une : elle est toujours renvoyée avec sa provenance (« proposé »,
« global » ou « campagne ») et sa règle écrite, jamais présentée comme une
prédiction.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campagne import CampagneJour, CampagneObligation, SeuilAlerte
from app.models.session import ProgramSession, SessionStatus

SEUILS_PROPOSES: dict[str, tuple[float, str, str]] = {
    # code: (valeur, unité, règle)
    "absences_taux": (20.0, "%", "Élève signalé si ses absences dépassent ce pourcentage de ses jours d'appel renseignés."),
    "absences_consecutives": (3.0, "jours", "Élève signalé après ce nombre de jours d'appel consécutifs en absence."),
    "retard_transmission_jours": (2.0, "jours", "Rapport en retard s'il est reçu plus de N jours après la date d'activité (en plus du délai de l'obligation)."),
    "visite_delai_jours": (30.0, "jours", "Classe signalée si aucune visite pédagogique n'a eu lieu depuis N jours."),
    "domaine_taux_min": (50.0, "%", "Domaine de la grille signalé si le taux moyen des visites est inférieur à ce seuil."),
}


async def session_ou_active(db: AsyncSession, session_id: Optional[uuid.UUID]) -> Optional[ProgramSession]:
    if session_id:
        s = (await db.execute(select(ProgramSession).where(ProgramSession.id == session_id))).scalar_one_or_none()
        if s is None:
            raise HTTPException(404, "Campagne introuvable.")
        return s
    return (await db.execute(
        select(ProgramSession).where(ProgramSession.status == SessionStatus.active, ProgramSession.archived_at.is_(None))
        .order_by(ProgramSession.date_debut.desc())
    )).scalars().first()


async def seuils(db: AsyncSession, session_id: Optional[uuid.UUID]) -> dict[str, dict]:
    rows = (await db.execute(select(SeuilAlerte).where(
        (SeuilAlerte.session_id == session_id) | SeuilAlerte.session_id.is_(None)
    ))).scalars().all()
    out = {code: {"code": code, "valeur": v, "unite": u, "regle": r, "provenance": "propose"}
           for code, (v, u, r) in SEUILS_PROPOSES.items()}
    for s in sorted(rows, key=lambda s: s.session_id is not None):  # global puis campagne
        base = out.get(s.code, {"code": s.code, "unite": "", "regle": None})
        out[s.code] = {**base, "valeur": s.valeur, "regle": s.regle or base.get("regle"),
                       "provenance": "campagne" if s.session_id else "global"}
    return out


async def jours_attendus(db: AsyncSession, session_id: uuid.UUID, debut: date, fin: date) -> list[date]:
    return list((await db.execute(select(CampagneJour.date_jour).where(
        CampagneJour.session_id == session_id, CampagneJour.attendu.is_(True),
        CampagneJour.date_jour >= debut, CampagneJour.date_jour <= fin,
    ).order_by(CampagneJour.date_jour))).scalars().all())


async def calendrier_configure(db: AsyncSession, session_id: uuid.UUID) -> bool:
    return (await db.execute(select(CampagneJour.id).where(CampagneJour.session_id == session_id).limit(1))).first() is not None


async def obligations(db: AsyncSession, session_id: uuid.UUID) -> list[CampagneObligation]:
    return list((await db.execute(select(CampagneObligation).where(
        CampagneObligation.session_id == session_id, CampagneObligation.active.is_(True),
    ))).scalars().all())


def generer_dates(debut: date, fin: date, jours_semaine: set[int], exceptions: dict[date, str]) -> list[tuple[date, bool, Optional[str]]]:
    """Jours de la période dont le jour de semaine (0 = lundi) est retenu."""
    out, d = [], debut
    while d <= fin:
        if d.weekday() in jours_semaine:
            motif = exceptions.get(d)
            out.append((d, motif is None, motif))
        d += timedelta(days=1)
    return out


def semaine_iso(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-S{w:02d}"
