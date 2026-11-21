"""
Service de l'historique des modifications (audit log).

Enregistre toute action de création / modification / suppression effectuée
par un utilisateur connecté sur les routes d'administration, et permet de
les lister pour la rubrique "Historique des modifications" du dashboard admin.

APP-13 : chaque entrée dit qui, quelle opération, sur quelle cible, pourquoi
(motif) et avec quelle référence de requête. Les opérations métier sensibles
(annulation ou correction de rapport, archivage, séance oubliée,
remplacement…) écrivent une entrée précise via `journaliser`, dans la même
transaction que la modification ; l'entrée générique du middleware n'est
alors pas ajoutée en double. Aucune valeur saisie n'est recopiée.
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any, Optional, Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User


# ── Libellés FR des ressources, déduits du préfixe de route ────────────────────
_ENTITY_LABELS: dict[str, str] = {
    "users":                 "Comptes utilisateurs",
    "teachers":              "Enseignants",
    "schools":               "Écoles",
    "planning":              "Planning",
    "sessions":              "Sessions",
    "superviseurs":          "Superviseurs",
    "evaluateurs":           "Évaluateurs",
    "eleves":                "Élèves",
    "classes":               "Classes",
    "rapports":              "Rapports",
    "rapports-journaliers":  "Rapports journaliers",
    "rapport-questions":     "Questions de rapport",
    "evaluation-competences": "Évaluations",
    "ressources":            "Ressources FLN",
    "suivi-seances":         "Suivi des séances",
    "suivi-superviseurs":    "Suivi superviseurs",
    "suivi-evaluations":     "Suivi évaluations",
    "suivi-plannings":       "Suivi plannings",
}

_ACTION_LABELS: dict[str, str] = {
    "create": "Création",
    "update": "Modification",
    "delete": "Suppression",
}

_METHOD_TO_ACTION: dict[str, str] = {
    "POST":   "create",
    "PUT":    "update",
    "PATCH":  "update",
    "DELETE": "delete",
}


def resolve_entity_label(path: str) -> str:
    """Déduit un libellé FR de ressource à partir du chemin d'API (ex: /api/v1/admin/teachers/123)."""
    parts = [p for p in path.split("/") if p]
    for part in parts:
        if part in _ENTITY_LABELS:
            return _ENTITY_LABELS[part]
    # repli : dernier segment non-UUID, formaté
    for part in reversed(parts):
        if part not in ("api", "v1", "admin") and not _looks_like_id(part):
            return part.replace("-", " ").replace("_", " ").title()
    return "Plateforme"


def _looks_like_id(segment: str) -> bool:
    return len(segment) >= 20 and "-" in segment


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def cible_depuis_chemin(path: str) -> Optional[str]:
    """Dernier identifiant présent dans le chemin (/admin/eleves/<id>/archiver → <id>)."""
    for part in reversed([p for p in path.split("/") if p]):
        if _UUID.match(part):
            return part
    return None


# ── Contexte de requête ────────────────────────────────────────────────────────
# Ouvert par le middleware d'historique : le dict est partagé avec la tâche qui
# exécute la route, qui y signale qu'une entrée précise a été écrite.
_contexte: ContextVar[Optional[dict[str, Any]]] = ContextVar("audit_contexte", default=None)


def ouvrir_contexte(state: Any) -> tuple[dict[str, Any], Any]:
    ctx: dict[str, Any] = {"state": state, "ecrit": False}
    return ctx, _contexte.set(ctx)


def fermer_contexte(jeton: Any) -> None:
    _contexte.reset(jeton)


def _request_id() -> Optional[str]:
    ctx = _contexte.get()
    return getattr(ctx["state"], "request_id", None) if ctx else None


def journaliser(
    db: AsyncSession,
    *,
    acteur: Optional[User],
    action: str,
    entity: str,
    description: str,
    cible_type: Optional[str] = None,
    cible_id: Any = None,
    motif: Optional[str] = None,
    method: str = "POST",
    path: str = "",
) -> AuditLog:
    """Entrée précise d'historique, ajoutée à la transaction en cours (sans commit)."""
    log = AuditLog(
        user_id=acteur.id if acteur else None,
        user_name=acteur.name if acteur else "système",
        user_role=(acteur.role.value if hasattr(acteur.role, "value") else str(acteur.role)) if acteur else "systeme",
        action=action,
        entity=entity,
        method=method.upper(),
        path=path[:255],
        description=description,
        cible_type=cible_type,
        cible_id=str(cible_id) if cible_id is not None else None,
        motif=(motif or "").strip() or None,
        request_id=_request_id(),
    )
    db.add(log)
    ctx = _contexte.get()
    if ctx is not None:
        ctx["ecrit"] = True
    return log


def deja_journalise() -> bool:
    ctx = _contexte.get()
    return bool(ctx and ctx["ecrit"])


def action_from_method(method: str) -> str | None:
    return _METHOD_TO_ACTION.get(method.upper())


async def log_action(
    db: AsyncSession,
    *,
    user: User,
    method: str,
    path: str,
    request_id: Optional[str] = None,
) -> None:
    """Enregistre une entrée d'historique pour une action de mutation réussie."""
    action = action_from_method(method)
    if action is None:
        return

    entity = resolve_entity_label(path)
    description = f"{_ACTION_LABELS[action]} — {entity}"

    log = AuditLog(
        user_id=user.id,
        user_name=user.name,
        user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
        action=action,
        entity=entity,
        method=method.upper(),
        path=path,
        description=description,
        cible_type=entity,
        cible_id=cible_depuis_chemin(path),
        request_id=request_id,
    )
    db.add(log)
    await db.commit()


async def list_audit_logs(
    db: AsyncSession, skip: int, limit: int
) -> tuple[int, Sequence[AuditLog]]:
    total = (await db.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    )
    return total, result.scalars().all()
