"""Protocole de recherche et accès au minuteur (lot L6, APP-33).

Matrice appliquée quand la campagne active du tuteur a `protocole_minuteur` :

    groupe_recherche   minuteur   app_access effectif
    traitement         visible    full (ou timer_only si l'accès est restreint)
    controle           masqué     no_timer (ou timer_only, minuteur masqué)
    non assigné        réglage individuel (app_access), signalé comme incohérence

Toute dérogation à la matrice est une EXCEPTION : datée, attribuée, motivée et
inscrite dans l'historique immuable `assignations_recherche`. Le serveur
calcule l'accès effectif et le transmet au mobile (`/auth/me`, `/app/sync`) :
l'écran n'est plus la seule source de vérité. Tant que `protocole_minuteur`
est désactivé, le comportement antérieur (réglage individuel) est conservé.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recherche import AssignationRecherche
from app.models.session import ProgramSession, TeacherSession
from app.models.user import User, UserGroupe, UserRole
from app.services.audit_service import journaliser
from app.services.progression_service import active_session_for

ACCES = ("full", "timer_only", "no_timer")


def acces_matrice(groupe: Optional[str], app_access: str) -> Optional[str]:
    """Accès attendu par le protocole, None si le tuteur n'est pas assigné."""
    if groupe is None:
        return None
    if app_access == "timer_only":
        return "timer_only"
    return "full" if groupe == UserGroupe.traitement.value else "no_timer"


def _groupe(u: User) -> Optional[str]:
    return u.groupe_recherche.value if u.groupe_recherche else None


async def derniere_assignation(db: AsyncSession, user_id: uuid.UUID) -> Optional[AssignationRecherche]:
    return (await db.execute(select(AssignationRecherche).where(AssignationRecherche.user_id == user_id)
                             .order_by(AssignationRecherche.effective_le.desc()).limit(1))).scalar_one_or_none()


async def acces_effectif(db: AsyncSession, user: User, session: Optional[ProgramSession] = None) -> dict:
    """{app_access, minuteur_visible, source, exception, session_id}."""
    stocke = user.app_access or "full"
    if user.role != UserRole.enseignant:
        return {"app_access": stocke, "minuteur_visible": stocke != "no_timer", "source": "reglage", "exception": False}
    session = session or await active_session_for(db, user)
    if session is None or not session.protocole_minuteur:
        return {"app_access": stocke, "minuteur_visible": stocke != "no_timer", "source": "reglage", "exception": False,
                "session_id": session.id if session else None}
    attendu = acces_matrice(_groupe(user), stocke)
    if attendu is None:
        return {"app_access": stocke, "minuteur_visible": stocke != "no_timer", "source": "non_assigne",
                "exception": False, "session_id": session.id}
    derniere = await derniere_assignation(db, user.id)
    if derniere is not None and derniere.exception and derniere.app_access == stocke and stocke != attendu:
        return {"app_access": stocke, "minuteur_visible": stocke != "no_timer", "source": "exception",
                "exception": True, "motif": derniere.motif, "session_id": session.id}
    visible = _groupe(user) == UserGroupe.traitement.value
    return {"app_access": attendu, "minuteur_visible": visible, "source": "protocole", "exception": False,
            "session_id": session.id}


async def tracer(db: AsyncSession, user: User, acteur: Optional[User], motif: Optional[str],
                 session_id: Optional[uuid.UUID] = None, exception: bool = False) -> None:
    db.add(AssignationRecherche(
        user_id=user.id, session_id=session_id, groupe_recherche=_groupe(user), app_access=user.app_access or "full",
        exception=exception, motif=motif, auteur_id=acteur.id if acteur else None,
    ))


async def apres_modification(db: AsyncSession, user: User, avant: tuple[Optional[str], str], acteur: Optional[User],
                             motif: Optional[str] = None) -> None:
    """Appelé après une mise à jour de compte : trace tout changement de
    groupe ou d'accès, et exige un motif pour une dérogation au protocole."""
    apres = (_groupe(user), user.app_access or "full")
    if apres == avant or user.role != UserRole.enseignant:
        return
    session = await active_session_for(db, user)
    attendu = acces_matrice(apres[0], apres[1])
    exception = bool(session and session.protocole_minuteur and attendu is not None and attendu != apres[1])
    if exception and not (motif or "").strip():
        raise HTTPException(422, {
            "code": "EXCEPTION_PROTOCOLE",
            "message": f"Accès « {apres[1]} » contraire au protocole (groupe {apres[0]} → {attendu}) : un motif est requis.",
        })
    await tracer(db, user, acteur, motif or "Modification du compte", session.id if session else None, exception)


async def matrice(db: AsyncSession, session: ProgramSession) -> dict:
    tuteurs = (await db.execute(
        select(User).join(TeacherSession, TeacherSession.teacher_id == User.id)
        .where(TeacherSession.session_id == session.id, User.role == UserRole.enseignant).order_by(User.name)
    )).scalars().all()
    lignes, comptes = [], {"traitement": 0, "controle": 0, "non_assigne": 0, "incoherents": 0, "exceptions": 0}
    for u in tuteurs:
        eff = await acces_effectif(db, u, session)
        attendu = acces_matrice(_groupe(u), u.app_access or "full")
        incoherent = attendu is None or (attendu != (u.app_access or "full") and not eff["exception"])
        comptes[_groupe(u) or "non_assigne"] += 1
        comptes["incoherents"] += int(incoherent)
        comptes["exceptions"] += int(eff["exception"])
        lignes.append({
            "user_id": u.id, "nom": u.name, "groupe_recherche": _groupe(u), "app_access_stocke": u.app_access,
            "app_access_attendu": attendu, "app_access_effectif": eff["app_access"],
            "minuteur_visible": eff["minuteur_visible"], "source": eff["source"], "exception": eff["exception"],
            "incoherent": incoherent,
        })
    return {"campagne": {"id": session.id, "nom": session.name, "protocole_minuteur": session.protocole_minuteur},
            "totaux": comptes, "lignes": lignes}


async def aligner(db: AsyncSession, session: ProgramSession, acteur: User, motif: str, dry_run: bool) -> dict:
    """Aligne `app_access` sur la matrice pour les tuteurs assignés, sauf exceptions tracées."""
    m = await matrice(db, session)
    a_changer = [l for l in m["lignes"] if l["incoherent"] and l["app_access_attendu"] is not None]
    non_assignes = [l for l in m["lignes"] if l["groupe_recherche"] is None]
    if not dry_run:
        for l in a_changer:
            u = (await db.execute(select(User).where(User.id == l["user_id"]))).scalar_one()
            u.app_access = l["app_access_attendu"]
            await tracer(db, u, acteur, motif, session.id)
        journaliser(db, acteur=acteur, action="alignement", entity="Protocole de recherche",
                    description=f"Alignement de {len(a_changer)} accès sur le protocole ({session.name})",
                    cible_type="session", cible_id=session.id, motif=motif, path="/recherche/aligner")
        await db.flush()
    return {"a_aligner": len(a_changer), "non_assignes": len(non_assignes), "applique": not dry_run,
            "lignes": a_changer}
