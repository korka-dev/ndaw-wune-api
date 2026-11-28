#!/usr/bin/env python3
"""
Affecte à chaque superviseur ses tuteurs, d'après l'onglet « Classes » de la
base acteurs/élèves 2026-2027 (Superviseur proposé → Enseignant proposé).

Les comptes sont retrouvés par numéro de téléphone. L'affectation est la même
que celle du bouton « Gérer » de Gestion Superviseurs : le superviseur voit
ses tuteurs dans l'app à sa prochaine synchronisation.

Usage — dans le conteneur backend (dossier /app)
    python scripts/affecter_superviseurs_tuteurs.py /tmp/base.xlsx                      simulation
    python scripts/affecter_superviseurs_tuteurs.py /tmp/base.xlsx --apply              enregistre
    … --remplacer   remplace les affectations existantes au lieu d'ajouter
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
from app.services import affectation_superviseurs_service as service  # noqa: E402
from app.services.audit_service import journaliser  # noqa: E402


async def run(args) -> None:
    mode = "remplacement" if args.remplacer else "ajout"
    print(f"\n{'' if args.apply else '[SIMULATION] '}Affectation superviseurs → tuteurs ({mode}) — {args.fichier}\n" + "=" * 72)
    try:
        lignes = service.lire(Path(args.fichier).read_bytes())
    except HTTPException as e:
        sys.exit(f"❌  {e.detail}")
    print(f"Classes lues : {len(lignes)}")
    async with AsyncSessionLocal() as db:
        r = await service.traiter(db, lignes, remplacer=args.remplacer)

        print(f"\n✅  Superviseurs affectés : {len(r['affectes'])}")
        for x in r["affectes"]:
            print(f"     {x['superviseur']:32s} {x['telephone']}  {x['avant']} → {x['apres']} tuteur(s) : {', '.join(x['tuteurs'])}")
        print(f"\n=   Déjà à jour : {len(r['inchanges'])}")
        for x in r["inchanges"]:
            print(f"     {x['superviseur']:32s} {x['telephone']}  {x['apres']} tuteur(s)")
        print(f"\n❓  Superviseurs sans compte (numéro introuvable) : {len(r['superviseurs_introuvables'])}")
        for x in r["superviseurs_introuvables"]:
            print(f"     {x['nom']:32s} {x['telephone']}  {x['ecole']} ({x['nb_tuteurs']} tuteur(s))")
        print(f"\n❓  Tuteurs sans compte (numéro introuvable) : {len(r['tuteurs_introuvables'])}")
        for x in r["tuteurs_introuvables"]:
            print(f"     {x['nom']:32s} {x['telephone']}  {x['ecole']} (superviseur : {x['superviseur']})")
        print(f"\n⚠️   Tuteurs déjà suivis par un autre superviseur : {len(r['deja_suivis_ailleurs'])}")
        for x in r["deja_suivis_ailleurs"]:
            print(f"     {x['tuteur']:32s} actuel : {x['superviseur_actuel']} | fichier : {x['superviseur_fichier']}")
        print(f"\n⏭️   Classes incomplètes (tuteur ou superviseur manquant) : {len(r['classes_incompletes'])}")
        for x in r["classes_incompletes"]:
            print(f"     classe {x['classe']}  {x['ecole']}")

        if not args.apply:
            await db.rollback()
            print("\n🧪  SIMULATION — rien n'a été écrit. Relancez avec --apply pour enregistrer.")
            return
        if r["affectes"]:
            journaliser(db, acteur=None, action="update", entity="Superviseur",
                        path="scripts/affecter_superviseurs_tuteurs.py",
                        description=f"Affectation superviseurs → tuteurs ({mode}) : "
                                    f"{len(r['affectes'])} superviseur(s) mis à jour depuis {Path(args.fichier).name}.")
        await db.commit()
        print("\n✅  Enregistré. Les superviseurs voient leurs tuteurs à la prochaine synchronisation de l'app.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fichier")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--remplacer", action="store_true", help="remplace les affectations existantes au lieu d'ajouter")
    asyncio.run(run(ap.parse_args()))
