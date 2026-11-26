"""Comptes inspecteurs depuis l'onglet « Points_focaux » de la base 2026-2027 (lot L10).

Utilisé par le bouton « Importer » de Gestion Inspecteurs et par
scripts/creer_inspecteurs_points_focaux.py.

Règles
    - IEF : colonne « IEF harmonisée ». Un point focal sans IEF harmonisée
      (IA Podor, Saint-Louis à confirmer) n'est créé que si l'admin choisit
      son IEF (`rattachements`). Rien n'est deviné ; « IEF proposée » n'est
      qu'une suggestion affichée.
    - L'IEF doit porter au moins une école active (import de la base fait).
    - Téléphone = identifiant de connexion :
        · déjà un compte inspecteur → nom et IEF mis à jour (restauré s'il
          était archivé) ;
        · déjà un compte d'un AUTRE rôle → ignoré et signalé, jamais modifié.
    - Nouveau compte : mot de passe temporaire habituel (DEFAULT_USER_PASSWORD),
      à changer à la première connexion, comme depuis le dashboard.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.core.upload_utils import iter_rows_capped, load_workbook_safe
from app.models.user import User, UserRole, UserStatus
from app.schemas.user import _normalize_sn_phone
from app.services.inspecteur_service import ief_canonique

FEUILLE = "Points_focaux"
_COLONNES = ("ID point focal", "Nom repris", "Téléphone national", "IEF harmonisée")


def _txt(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = " ".join(str(v).split())
    return s or None


def lire(content: bytes) -> list[dict]:
    """Lignes de l'onglet « Points_focaux » ; 422 si l'onglet ou ses colonnes manquent."""
    wb = load_workbook_safe(content)
    try:
        if FEUILLE not in wb.sheetnames:
            raise HTTPException(status_code=422, detail=(
                f"Onglet « {FEUILLE} » introuvable : importez la base 2026-2027 "
                f"(onglets du fichier : {', '.join(wb.sheetnames)})."))
        lignes = list(iter_rows_capped(wb[FEUILLE]))
    finally:
        wb.close()
    if not lignes:
        raise HTTPException(status_code=422, detail=f"L'onglet « {FEUILLE} » est vide.")
    entete = [_txt(h) for h in lignes[0]]
    manquantes = [c for c in _COLONNES if c not in entete]
    if manquantes:
        raise HTTPException(status_code=422, detail=f"Colonnes manquantes dans « {FEUILLE} » : {manquantes}.")
    return [dict(zip(entete, row)) for row in lignes[1:] if any(v is not None for v in row)]


async def traiter(db: AsyncSession, points: list[dict], rattachements: Optional[dict[str, str]] = None,
                  exclus: Optional[set[str]] = None) -> dict:
    """Crée / met à jour les comptes dans la session (sans valider : l'appelant
    valide ou annule). Retourne le détail ligne par ligne."""
    choix = {k.strip().upper(): (v or "").strip() for k, v in (rattachements or {}).items()}
    exclus = {e.strip().upper() for e in (exclus or set())}
    hash_defaut: list[str] = []
    crees, mis_a_jour, inchanges, a_rattacher, ignores = [], [], [], [], []

    for p in points:
        pf = (_txt(p.get("ID point focal")) or "?").upper()
        nom = _txt(p.get("Nom repris")) or _txt(p.get("Libellé du contact"))
        base = {"pf": pf, "nom": nom, "structure": _txt(p.get("Structure indiquée")),
                "fonction": _txt(p.get("Fonction indiquée"))}
        if pf in exclus:
            ignores.append({**base, "raison": "Laissé de côté."})
            continue
        try:
            tel = _normalize_sn_phone(_txt(p.get("Téléphone national")))
        except ValueError:
            tel = None
        base["telephone"] = tel
        if not tel or not nom:
            ignores.append({**base, "raison": "Nom ou téléphone (9 chiffres) manquant."})
            continue

        ief_saisie = choix.get(pf) or _txt(p.get("IEF harmonisée"))
        if not ief_saisie:
            a_rattacher.append({**base, "ief_proposee": _txt(p.get("IEF proposée")),
                                "observation": _txt(p.get("Observation"))})
            continue
        try:
            ief = await ief_canonique(db, ief_saisie)
        except HTTPException:
            ignores.append({**base, "raison": f"Aucune école active pour « {ief_saisie} » : importez d'abord "
                                              f"la base de l'année (Import / Export)."})
            continue
        base["ief"] = ief

        existant = (await db.execute(select(User).where(User.phone == tel))).scalar_one_or_none()
        if existant is not None:
            if existant.role != UserRole.inspecteur:
                ignores.append({**base, "raison": f"Numéro déjà utilisé par le compte {existant.role.value} "
                                                  f"« {existant.name} » : non modifié."})
                continue
            avant = (existant.name, existant.ief, existant.archived_at)
            existant.name, existant.ief = nom, ief
            if existant.archived_at is not None:
                existant.archived_at, existant.status = None, UserStatus.actif
            (mis_a_jour if avant != (existant.name, existant.ief, existant.archived_at) else inchanges).append(
                {**base, "id": str(existant.id)})
            continue

        if not hash_defaut:
            hash_defaut.append(hash_password(settings.DEFAULT_USER_PASSWORD))
        u = User(id=uuid.uuid4(), name=nom, phone=tel, ief=ief,
                 title=f"Inspecteur — point focal {ief}", role=UserRole.inspecteur, status=UserStatus.actif,
                 app_access="full", password_hash=hash_defaut[0], must_change_password=True)
        db.add(u)
        await db.flush()
        crees.append({**base, "id": str(u.id)})

    return {"crees": crees, "mis_a_jour": mis_a_jour, "inchanges": inchanges,
            "a_rattacher": a_rattacher, "ignores": ignores}
