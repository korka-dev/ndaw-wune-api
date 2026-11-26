#!/usr/bin/env python3
"""
Comptes inspecteurs depuis l'onglet « Points_focaux » de la base 2026-2027 (lot L10)
====================================================================================

Lit l'onglet « Points_focaux » (colonnes « ID point focal », « Nom repris »,
« Téléphone national », « IEF harmonisée », « IEF proposée », « Fonction
indiquée ») et crée un compte INSPECTEUR par point focal rattaché à une IEF.

Règles
    - IEF : « IEF harmonisée ». Un point focal sans IEF harmonisée (IA Podor,
      Saint-Louis à confirmer) est IGNORÉ, sauf si --ief PF-xxx="IEF …" le
      rattache explicitement. Rien n'est deviné.
    - L'IEF doit porter au moins une école active en base : faites d'abord
      l'import de la base 2026-2027.
    - Téléphone = identifiant de connexion (9 chiffres).
        · déjà un compte inspecteur avec ce numéro → nom et IEF mis à jour ;
        · déjà un compte d'un AUTRE rôle → ignoré, signalé (jamais modifié).
    - Nouveau compte : mot de passe temporaire habituel (DEFAULT_USER_PASSWORD),
      à changer à la première connexion, comme depuis le dashboard.

Usage — dans le conteneur backend (dossier /app)
    python scripts/creer_inspecteurs_points_focaux.py /tmp/base.xlsx
        Simulation : n'écrit rien, affiche ce qui serait fait.
    python scripts/creer_inspecteurs_points_focaux.py /tmp/base.xlsx --apply
        Création réelle.
    … --ief PF-007="IEF Saint-Louis-commune"     (répétable)
        Rattache un point focal sans IEF harmonisée.
    … --exclure PF-010                            (répétable)
        Ignore un point focal.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.user import User, UserRole, UserStatus  # noqa: E402
from app.schemas.user import _normalize_sn_phone  # noqa: E402
from app.services.audit_service import journaliser  # noqa: E402
from app.services.inspecteur_service import ief_canonique  # noqa: E402

FEUILLE = "Points_focaux"


def _txt(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = " ".join(str(v).split())
    return s or None


def lire_points_focaux(chemin: str) -> list[dict]:
    wb = load_workbook(chemin, read_only=True, data_only=True)
    if FEUILLE not in wb.sheetnames:
        sys.exit(f"❌  Onglet « {FEUILLE} » introuvable (onglets : {', '.join(wb.sheetnames)}).")
    lignes = list(wb[FEUILLE].iter_rows(values_only=True))
    wb.close()
    entete = [_txt(h) for h in lignes[0]]
    manquantes = [c for c in ("ID point focal", "Nom repris", "Téléphone national", "IEF harmonisée")
                  if c not in entete]
    if manquantes:
        sys.exit(f"❌  Colonnes manquantes dans « {FEUILLE} » : {manquantes}")
    return [dict(zip(entete, row)) for row in lignes[1:] if any(v is not None for v in row)]


def _rattachements(valeurs: list[str]) -> dict[str, str]:
    out = {}
    for v in valeurs:
        if "=" not in v:
            sys.exit(f"❌  --ief attend PF-xxx=\"IEF …\" (reçu : {v})")
        pf, ief = v.split("=", 1)
        out[pf.strip().upper()] = ief.strip()
    return out


async def run(args) -> None:
    mode = "" if args.apply else "[SIMULATION] "
    print(f"\n{mode}Comptes inspecteurs — {args.fichier}\n" + "=" * 72)
    points = lire_points_focaux(args.fichier)
    forces = _rattachements(args.ief)
    exclus = {p.strip().upper() for p in args.exclure}
    hash_defaut = hash_password(settings.DEFAULT_USER_PASSWORD)

    crees, maj, ignores = [], [], []
    async with AsyncSessionLocal() as db:
        for p in points:
            pf = (_txt(p.get("ID point focal")) or "?").upper()
            nom = _txt(p.get("Nom repris")) or _txt(p.get("Libellé du contact"))
            libelle = f"{pf} {nom or '(sans nom)'}"
            if pf in exclus:
                ignores.append((libelle, "exclu (--exclure)"))
                continue
            ief_saisie = forces.get(pf) or _txt(p.get("IEF harmonisée"))
            if not ief_saisie:
                proposee = _txt(p.get("IEF proposée"))
                conseil = f' — rattacher avec --ief {pf}="{proposee}"' if proposee else ""
                ignores.append((libelle, f"aucune IEF harmonisée ({_txt(p.get('Structure indiquée'))}){conseil}"))
                continue
            try:
                ief = await ief_canonique(db, ief_saisie)
            except HTTPException:
                ignores.append((libelle, f"aucune école active pour « {ief_saisie} » (import de la base fait ?)"))
                continue
            try:
                tel = _normalize_sn_phone(_txt(p.get("Téléphone national")))
            except ValueError:
                tel = None
            if not tel or not nom:
                ignores.append((libelle, "nom ou téléphone (9 chiffres) manquant"))
                continue
            titre = "Inspecteur de l'enseignement élémentaire — point focal " + ief

            existant = (await db.execute(select(User).where(User.phone == tel))).scalar_one_or_none()
            if existant is not None:
                if existant.role != UserRole.inspecteur:
                    ignores.append((libelle, f"numéro déjà utilisé par le compte {existant.role.value} "
                                             f"« {existant.name} » : non modifié"))
                    continue
                avant = (existant.name, existant.ief, existant.archived_at)
                existant.name, existant.ief = nom, ief
                if existant.archived_at is not None:
                    existant.archived_at, existant.status = None, UserStatus.actif
                if avant != (existant.name, existant.ief, existant.archived_at):
                    maj.append((libelle, tel, ief))
                continue
            db.add(User(id=uuid.uuid4(), name=nom, phone=tel, title=titre, ief=ief,
                        role=UserRole.inspecteur, status=UserStatus.actif, app_access="full",
                        password_hash=hash_defaut, must_change_password=True))
            crees.append((libelle, tel, ief))

        print(f"\n✅  À créer : {len(crees)}")
        for l, t, i in crees:
            print(f"     {l:40s} {t}  → {i}")
        print(f"\n🔁  Déjà présents, mis à jour : {len(maj)}")
        for l, t, i in maj:
            print(f"     {l:40s} {t}  → {i}")
        print(f"\n⏭️   Ignorés : {len(ignores)}")
        for l, raison in ignores:
            print(f"     {l:40s} {raison}")

        if not args.apply:
            await db.rollback()
            print("\n🧪  SIMULATION — rien n'a été écrit. Relancez avec --apply pour créer les comptes.")
            return
        journaliser(db, acteur=None, action="create", entity="Inspecteur",
                    path="scripts/creer_inspecteurs_points_focaux.py",
                    description=f"Points focaux 2026-2027 : {len(crees)} compte(s) inspecteur créé(s), "
                                f"{len(maj)} mis à jour, {len(ignores)} ignoré(s).")
        await db.commit()
        print("\n✅  Enregistré. Les nouveaux inspecteurs se connectent avec leur numéro et le mot de passe "
              "temporaire habituel, à changer à la première connexion.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fichier")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ief", action="append", default=[], metavar='PF-xxx="IEF …"')
    ap.add_argument("--exclure", action="append", default=[], metavar="PF-xxx")
    asyncio.run(run(ap.parse_args()))
