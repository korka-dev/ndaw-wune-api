"""
Service de l'historique des modifications (audit log).

Enregistre toute action de création / modification / suppression effectuée
par un utilisateur connecté sur les routes d'administration, et permet de
les lister pour la rubrique "Historique des modifications" du dashboard admin.
"""
from __future__ import annotations

from typing import Sequence

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


def action_from_method(method: str) -> str | None:
    return _METHOD_TO_ACTION.get(method.upper())


async def log_action(
    db: AsyncSession,
    *,
    user: User,
    method: str,
    path: str,
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
