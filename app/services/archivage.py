"""Archivage au lieu de suppression (rapport V2, APP-15).

Règle commune aux écoles, classes, élèves, comptes et campagnes :

- « Supprimer » dans l'administration ARCHIVE : l'objet disparaît des listes
  et des parcours terrain, mais toutes ses observations (séances, rapports,
  évaluations, pointages, remplacements) sont conservées. Restauration possible.
- La suppression définitive (`definitif=true`) n'est permise que si aucun
  historique n'y est rattaché ; sinon 409 `HISTORIQUE_PRESENT` avec le détail.
  En base, les clés étrangères des observations sont en RESTRICT : même une
  suppression SQL directe ne peut plus effacer l'historique en cascade.

Effets des cascades restantes (configuration, pas d'observation) : supprimer
définitivement une école supprime ses classes et sa configuration de
progression ; une campagne, son planning, ses affectations et sa configuration.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.models.evaluation_eleve import EvaluationEleve
from app.models.evaluation_resultat import EvaluationResultat
from app.models.evaluation_tirage import EvaluationTirage
from app.models.rapport_journalier import RapportJournalier
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.seance import RapportProf, Seance
from app.models.session import ProgramSession, SessionStatus
from app.models.supervisor_presence import SupervisorPresenceCheck
from app.models.user import User, UserStatus

LIBELLES = {
    School: "École", SchoolClasse: "Classe", Eleve: "Élève", User: "Compte", ProgramSession: "Campagne",
}


def non_archive(model, inclure_archives: bool = False):
    """Clause de liste : objets actifs seulement, sauf demande explicite."""
    return true() if inclure_archives else model.archived_at.is_(None)


async def _compte(db: AsyncSession, model, *conditions) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(or_(*conditions)))).scalar_one()


async def historique(db: AsyncSession, obj: Any) -> dict[str, int]:
    """Observations rattachées à l'objet (seules les valeurs non nulles)."""
    c: dict[str, int] = {}
    if isinstance(obj, School):
        c["eleves"] = await _compte(db, Eleve, Eleve.school_id == obj.id)
        c["rapports_journaliers"] = await _compte(db, RapportJournalier, RapportJournalier.ecole_id == obj.id)
    elif isinstance(obj, ProgramSession):
        c["seances"] = await _compte(db, Seance, Seance.session_id == obj.id)
        c["rapports_journaliers"] = await _compte(db, RapportJournalier, RapportJournalier.session_id == obj.id)
        c["pointages"] = await _compte(db, SupervisorPresenceCheck, SupervisorPresenceCheck.session_id == obj.id)
        c["evaluations"] = await _compte(db, EvaluationEleve, EvaluationEleve.session_id == obj.id)
        c["eleves"] = await _compte(db, Eleve, Eleve.session_id == obj.id)
    elif isinstance(obj, User):
        c["seances"] = await _compte(db, Seance, Seance.teacher_id == obj.id)
        c["rapports_seance"] = await _compte(db, RapportProf, RapportProf.teacher_id == obj.id)
        c["rapports_journaliers"] = await _compte(
            db, RapportJournalier,
            RapportJournalier.teacher_id == obj.id, RapportJournalier.superviseur_id == obj.id,
        )
        c["evaluations"] = await _compte(db, EvaluationEleve, EvaluationEleve.superviseur_id == obj.id)
        c["resultats_tirage"] = await _compte(db, EvaluationResultat, EvaluationResultat.superviseur_id == obj.id)
        c["pointages"] = await _compte(
            db, SupervisorPresenceCheck,
            SupervisorPresenceCheck.superviseur_id == obj.id, SupervisorPresenceCheck.teacher_id == obj.id,
        )
        c["remplacements"] = await _compte(db, EleveRemplacement, EleveRemplacement.teacher_id == obj.id)
    elif isinstance(obj, Eleve):
        c["evaluations"] = await _compte(db, EvaluationEleve, EvaluationEleve.eleve_id == obj.id)
        c["tirages"] = await _compte(db, EvaluationTirage, EvaluationTirage.eleve_id == obj.id)
        c["remplacements"] = await _compte(
            db, EleveRemplacement,
            EleveRemplacement.ancien_eleve_id == obj.id, EleveRemplacement.nouveau_eleve_id == obj.id,
        )
    elif isinstance(obj, SchoolClasse):
        c["eleves"] = await _compte(
            db, Eleve, (Eleve.school_id == obj.school_id) & (func.lower(Eleve.classe) == func.lower(obj.name)),
        )
    return {k: v for k, v in c.items() if v}


def _audit(db: AsyncSession, actor: Optional[User], action: str, obj: Any, description: str) -> None:
    db.add(AuditLog(
        user_id=actor.id if actor else None,
        user_name=actor.name if actor else "système",
        user_role=actor.role.value if actor else "systeme",
        action=action,
        entity=LIBELLES.get(type(obj), type(obj).__name__),
        method="DELETE" if action in ("archive", "delete") else "POST",
        path=f"/admin/{type(obj).__tablename__}/{obj.id}",
        description=description,
    ))


async def archiver(db: AsyncSession, obj: Any, actor: Optional[User] = None) -> dict:
    """Archive (idempotent). Un compte archivé est aussi désactivé, une
    campagne archivée désactivée, un élève archivé sort des listes terrain."""
    now = datetime.now(timezone.utc)
    if obj.archived_at is None:
        obj.archived_at = now
        if isinstance(obj, User):
            obj.status = UserStatus.inactif
            obj.tokens_valid_from = now  # jetons en cours révoqués
        elif isinstance(obj, ProgramSession):
            obj.status = SessionStatus.inactive
        elif isinstance(obj, Eleve):
            obj.statut = "archive"
        _audit(db, actor, "archive", obj, f"Archivage ({getattr(obj, 'name', None) or getattr(obj, 'nom', '')})")
        await db.flush()
    _invalider(db)
    return {
        "id": str(obj.id), "archive": True, "archived_at": obj.archived_at.isoformat(),
        "historique_conserve": await historique(db, obj),
        "message": f"{LIBELLES.get(type(obj), 'Élément')} archivé : son historique est conservé. Restauration possible.",
    }


async def restaurer(db: AsyncSession, obj: Any, actor: Optional[User] = None) -> dict:
    if obj.archived_at is not None:
        obj.archived_at = None
        if isinstance(obj, User):
            obj.status = UserStatus.actif
        elif isinstance(obj, Eleve) and obj.statut == "archive":
            obj.statut = "actif"
        _audit(db, actor, "restore", obj, "Restauration après archivage")
        await db.flush()
    _invalider(db)
    return {"id": str(obj.id), "archive": False, "message": "Élément restauré."}


async def supprimer_definitivement(db: AsyncSession, obj: Any, actor: Optional[User] = None) -> dict:
    """Suppression physique, refusée (409) dès qu'un historique est rattaché."""
    h = await historique(db, obj)
    if h:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "HISTORIQUE_PRESENT",
                "message": "Suppression définitive impossible : des observations y sont rattachées. "
                           "Archivez plutôt (l'historique est conservé).",
                "historique": h,
            },
        )
    _audit(db, actor, "delete", obj, "Suppression définitive (aucun historique rattaché)")
    await db.delete(obj)
    await db.flush()
    _invalider(db)
    return {"id": str(obj.id), "supprime": True}


async def supprimer_ou_archiver(db: AsyncSession, obj: Any, actor: Optional[User], definitif: bool) -> dict:
    return await (supprimer_definitivement if definitif else archiver)(db, obj, actor)


async def charger(db: AsyncSession, model, obj_id: uuid.UUID, introuvable: str):
    obj = (await db.execute(select(model).where(model.id == obj_id))).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=introuvable)
    return obj


def _invalider(db: AsyncSession) -> None:
    from app.core.redis import invalidate_sync_caches_after_commit
    invalidate_sync_caches_after_commit(db)
