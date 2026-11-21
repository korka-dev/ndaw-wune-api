"""Journaux sans contenu personnel (rapport V2, APP-13).

Les journaux ne gardent que des métadonnées utiles au support : type
d'exception, code SQL, nom de contrainte, emplacement dans le code et
référence de requête. Jamais les valeurs saisies : un message d'erreur SQL
ou Pydantic peut contenir un nom, un téléphone ou une observation.
"""
from __future__ import annotations

import traceback
from typing import Any, Iterable


def resume_erreur(exc: BaseException) -> str:
    """« IntegrityError/UniqueViolationError sqlstate=23505 contrainte=uq_x » : sans message ni valeur."""
    parties = [type(exc).__name__]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parties[0] += f"/{type(orig).__name__}"
    cause = orig if orig is not None else exc
    for source in (cause, getattr(cause, "__cause__", None)):
        if source is None:
            continue
        code = getattr(source, "sqlstate", None) or getattr(source, "pgcode", None)
        if code:
            parties.append(f"sqlstate={code}")
        contrainte = getattr(source, "constraint_name", None)
        if contrainte:
            parties.append(f"contrainte={contrainte}")
        if code or contrainte:
            break
    return " ".join(parties)


def pile(exc: BaseException, limite: int = 12) -> str:
    """Emplacement de l'erreur (fichier, ligne, fonction), sans le message ni les variables."""
    cadres = traceback.extract_tb(exc.__traceback__)[-limite:]
    return " ← ".join(f"{c.filename.rsplit('/', 2)[-1]}:{c.lineno} {c.name}" for c in reversed(cadres))


def erreurs_validation(erreurs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Erreurs Pydantic sans la valeur soumise (`input`) ni le contexte brut (`ctx`).

    Le message (`msg`) décrit la règle (« doit contenir au plus 20 caractères ») ;
    les valeurs du `ctx` sont des bornes de validation, renvoyées sous forme texte.
    """
    sures = []
    for e in erreurs:
        item = {
            "loc": list(e.get("loc") or ()),
            "msg": str(e.get("msg", "")),
            "type": str(e.get("type", "")),
        }
        ctx = e.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {k: (v if isinstance(v, (int, float, bool)) else str(v)) for k, v in ctx.items()
                           if not isinstance(v, BaseException)}
        sures.append(item)
    return sures


def champs_en_erreur(erreurs: Iterable[dict[str, Any]]) -> str:
    """« body.nom:string_too_long, body.date:date_parsing » pour le journal."""
    return ", ".join(".".join(str(p) for p in e.get("loc", ())) + f":{e.get('type', '')}" for e in erreurs)
