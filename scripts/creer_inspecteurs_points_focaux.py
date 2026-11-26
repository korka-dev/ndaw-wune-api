#!/usr/bin/env python3
"""
Comptes inspecteurs depuis l'onglet « Points_focaux » de la base 2026-2027 (lot L10)
====================================================================================

Même traitement que le bouton « Importer » de Gestion Inspecteurs
(app/services/points_focaux_service.py), en ligne de commande.

Usage — dans le conteneur backend (dossier /app)
    python scripts/creer_inspecteurs_points_focaux.py /tmp/base.xlsx           simulation
    python scripts/creer_inspecteurs_points_focaux.py /tmp/base.xlsx --apply   création
    … --ief PF-007="IEF Saint-Louis-commune"   rattache un point focal sans IEF (répétable)
    … --exclure PF-010                         laisse un point focal de côté (répétable)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.services import points_focaux_service  # noqa: E402
from app.services.audit_service import journaliser  # noqa: E402


async def run(args) -> None:
    print(f"\n{'' if args.apply else '[SIMULATION] '}Comptes inspecteurs — {args.fichier}\n" + "=" * 72)
    try:
        points = points_focaux_service.lire(Path(args.fichier).read_bytes())
    except HTTPException as e:
        sys.exit(f"❌  {e.detail}")
    choix = {}
    for v in args.ief:
        if "=" not in v:
            sys.exit(f'❌  --ief attend PF-xxx="IEF …" (reçu : {v})')
        pf, ief = v.split("=", 1)
        choix[pf] = ief
    async with AsyncSessionLocal() as db:
        r = await points_focaux_service.traiter(db, points, choix, set(args.exclure))
        for titre, cle in (("✅  Créés", "crees"), ("🔁  Mis à jour", "mis_a_jour"), ("=   Inchangés", "inchanges")):
            print(f"\n{titre} : {len(r[cle])}")
            for x in r[cle]:
                print(f"     {x['pf']} {x['nom']:32s} {x['telephone']}  → {x['ief']}")
        print(f"\n❓  Sans IEF (à rattacher avec --ief) : {len(r['a_rattacher'])}")
        for x in r["a_rattacher"]:
            conseil = f' — proposition : --ief {x["pf"]}="{x["ief_proposee"]}"' if x.get("ief_proposee") else ""
            print(f"     {x['pf']} {x['nom']:32s} {x['structure']}{conseil}")
        print(f"\n⏭️   Ignorés : {len(r['ignores'])}")
        for x in r["ignores"]:
            print(f"     {x['pf']} {x['nom'] or '':32s} {x['raison']}")
        if not args.apply:
            await db.rollback()
            print("\n🧪  SIMULATION — rien n'a été écrit. Relancez avec --apply pour créer les comptes.")
            return
        if r["crees"] or r["mis_a_jour"]:
            journaliser(db, acteur=None, action="create", entity="Inspecteur",
                        path="scripts/creer_inspecteurs_points_focaux.py",
                        description=f"Points focaux : {len(r['crees'])} compte(s) inspecteur créé(s), "
                                    f"{len(r['mis_a_jour'])} mis à jour.")
        await db.commit()
        print("\n✅  Enregistré. Connexion avec le numéro et le mot de passe temporaire habituel.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fichier")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ief", action="append", default=[], metavar='PF-xxx="IEF …"')
    ap.add_argument("--exclure", action="append", default=[], metavar="PF-xxx")
    asyncio.run(run(ap.parse_args()))
