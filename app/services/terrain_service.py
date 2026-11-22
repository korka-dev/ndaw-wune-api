"""Lot L7 — règles terrain issues des retours des tuteurs et superviseurs.

Toutes les règles sont des réglages (`.env`) dont la valeur par défaut
conserve le comportement existant ; ARED les ajuste sans nouvelle version.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.models.terrain import ContactApp
from app.models.user import User

logger = logging.getLogger(__name__)


def parametres_terrain() -> dict:
    """Règles transmises aux téléphones à chaque synchronisation."""
    return {
        "remplacement_validation": settings.REMPLACEMENT_VALIDATION,
        "photo_rapport_obligatoire": settings.PHOTO_RAPPORT_OBLIGATOIRE,
        "minuteur_prealerte_secondes": settings.MINUTEUR_PREALERTE_SECONDES,
        "minuteur_son": settings.MINUTEUR_SON,
        "proposition_groupe_active": settings.PROPOSITION_GROUPE_ACTIVE,
        "support_contact": settings.SUPPORT_CONTACT or None,
        "systemes_supportes": settings.SYSTEMES_SUPPORTES or None,
        "rappels_max_par_jour": settings.RAPPELS_MAX_PAR_JOUR,
        "latest_app_version": settings.LATEST_APP_VERSION or None,
    }


def _version_propre(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return v[:20] if v and all(c.isalnum() or c in ".-+" for c in v) else None


async def enregistrer_contact(db: AsyncSession, user: User, version: Optional[str],
                              minuteur_expose: Optional[bool], app_access: Optional[str]) -> None:
    """RET15/RET13 : dernière synchronisation connue (best-effort, jamais bloquant)."""
    try:
        valeurs = dict(version_app=_version_propre(version), derniere_sync_le=datetime.now(timezone.utc),
                       minuteur_expose=minuteur_expose, app_access_expose=(app_access or None))
        stmt = pg_insert(ContactApp).values(user_id=user.id, **valeurs)
        await db.execute(stmt.on_conflict_do_update(index_elements=[ContactApp.user_id], set_=valeurs))
        await db.commit()
    except Exception as exc:  # pragma: no cover - ne doit jamais empêcher la synchronisation
        await db.rollback()
        logger.warning("[Terrain] contact non enregistré : %s", exc)


def _nom(e: Eleve) -> str:
    return f"{e.prenom} {e.nom}".strip() if e.prenom else e.nom


def _norm(c: Optional[str]) -> str:
    return " ".join((c or "").strip().split()).lower()


async def verifier_et_verrouiller(db: AsyncSession, rempl: EleveRemplacement) -> tuple[Eleve, Eleve]:
    """Recontrôle au moment de l'application : les deux élèves sont toujours disponibles."""
    ids = sorted({rempl.ancien_eleve_id, rempl.nouveau_eleve_id}, key=str)
    rows = (await db.execute(select(Eleve).where(Eleve.id.in_(ids)).order_by(Eleve.id).with_for_update())).scalars().all()
    par_id = {e.id: e for e in rows}
    ancien, nouveau = par_id.get(rempl.ancien_eleve_id), par_id.get(rempl.nouveau_eleve_id)
    if ancien is None or ancien.statut != "actif":
        raise HTTPException(status_code=409, detail="L'élève à remplacer n'est plus actif : la demande ne peut plus être appliquée.")
    if (nouveau is None or nouveau.statut != "actif"
            or (nouveau.statut_selection or "").strip().lower() != "remplaçant"
            or _norm(nouveau.classe) != _norm(ancien.classe)):
        raise HTTPException(status_code=409, detail="Le remplaçant n'est plus disponible pour cette classe.")
    return ancien, nouveau


def appliquer(rempl: EleveRemplacement, ancien: Eleve, nouveau: Eleve, decideur: Optional[User]) -> None:
    """Effet d'un remplacement : l'historique de présence de l'ancien élève est conservé (inactif, jamais supprimé)."""
    ancien.statut = "inactif"
    nouveau.statut_selection = "Titulaire"
    rempl.statut = "applique"
    if decideur is not None:
        rempl.decide_par_id = decideur.id
        rempl.decide_le = datetime.now(timezone.utc)


async def decider_remplacement(db: AsyncSession, rempl: EleveRemplacement, decision: str,
                               motif: Optional[str], acteur: User) -> EleveRemplacement:
    """RET03 : validation ou refus d'une demande en attente (superviseur ou admin)."""
    from app.core.redis import invalidate_sync_caches
    from app.services.audit_service import journaliser

    if rempl.statut != "en_attente":
        raise HTTPException(status_code=409, detail="Cette demande a déjà été traitée.")
    if decision == "refuser":
        if not (motif or "").strip():
            raise HTTPException(status_code=422, detail="Indiquez le motif du refus.")
        rempl.statut = "refuse"
        rempl.motif_refus = motif.strip()
        rempl.decide_par_id = acteur.id
        rempl.decide_le = datetime.now(timezone.utc)
    else:
        ancien, nouveau = await verifier_et_verrouiller(db, rempl)
        appliquer(rempl, ancien, nouveau, acteur)
    journaliser(
        db, acteur=acteur, action="update", entity="Remplacements élève",
        path="/remplacements/decision",
        description=(f"Remplacement {'validé' if decision == 'valider' else 'refusé'} — "
                     f"{rempl.ancien_eleve_nom} → {rempl.nouveau_eleve_nom} (classe {rempl.classe})"),
        cible_type="eleve", cible_id=rempl.ancien_eleve_id, motif=motif,
    )
    await db.commit()
    if decision == "valider":
        await invalidate_sync_caches()
    await db.refresh(rempl)
    return rempl


async def eleve_du_tuteur(db: AsyncSession, tuteur: User, eleve_id) -> Eleve:
    """Élève actif d'une classe affectée au tuteur (même périmètre que /app/sync), sinon 404."""
    from app.models.school import School
    from app.services.school_service import get_matching_school_ids

    eleve = await db.get(Eleve, eleve_id)
    school = await db.get(School, tuteur.school_id) if tuteur.school_id else None
    if eleve is None or school is None or not tuteur.classes:
        raise HTTPException(status_code=404, detail="Élève introuvable dans vos classes.")
    school_ids = await get_matching_school_ids(db, school)
    classes = {_norm(c) for c in tuteur.classes if c and c.strip()}
    if eleve.school_id not in school_ids or _norm(eleve.classe) not in classes or eleve.statut != "actif":
        raise HTTPException(status_code=404, detail="Élève introuvable dans vos classes.")
    return eleve
