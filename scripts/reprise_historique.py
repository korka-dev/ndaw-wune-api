"""Reprise de l'historique Kobo / CSV (rapport V2, Partie II D10.2).

À exécuter d'abord sur une COPIE ISOLÉE de la base (recette), jamais
directement en production. Aucune ligne source n'est modifiée ni supprimée.

    # 1. Figer une copie des sources (empreintes SHA-256)
    python -m scripts.reprise_historique figer --sources /chemin/Data --copie /srv/nwn-reprise/2024-25

    # 2. Analyser : en-têtes reconnus, doublons, codes à faire correspondre
    python -m scripts.reprise_historique analyser --copie /srv/nwn-reprise/2024-25 \
        --sortie correspondances_a_valider.csv

    #    → compléter la colonne cible_id du CSV (suggestions fournies), puis
    #      l'importer dans l'admin (Reprise → Correspondances) ou :
    python -m scripts.reprise_historique correspondances --fichier correspondances_validees.csv --par "Nom"

    # 3. Importer dans un lot (campagne choisie par ARED)
    python -m scripts.reprise_historique importer --copie /srv/nwn-reprise/2024-25 \
        --nom "Campagne 2024-25" --campagne <uuid> --par "Nom" [--simulation]

    # 4. Réconciliation : affichée à la fin de l'import et dans l'admin
    #    (Reprise → lot). --rapport fichier.json l'écrit aussi sur disque.

Options : --mapping fichier.json pour adapter les noms de colonnes (voir
MAPPING_DEFAUT dans app/services/reprise_service.py).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import AsyncSessionLocal
from app.models.reprise import CorrespondanceReprise, LotReprise
from app.services import reprise_service as rs


def _mapping(chemin: str | None) -> dict:
    if not chemin:
        return rs.MAPPING_DEFAUT
    perso = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return {k: {**rs.MAPPING_DEFAUT[k], **perso.get(k, {})} for k in rs.MAPPING_DEFAUT}


def figer(args) -> None:
    src, dst = Path(args.sources), Path(args.copie)
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"{dst} n'est pas vide : une copie figée ne s'écrase pas.")
    dst.mkdir(parents=True, exist_ok=True)
    manifeste = {"fige_le": datetime.now(timezone.utc).isoformat(), "origine": str(src), "fichiers": []}
    for nom in rs.FICHIERS_DEFAUT.values():
        p = src / nom
        if not p.exists():
            print(f"  absent : {nom}")
            continue
        shutil.copy2(p, dst / nom)
        (dst / nom).chmod(0o444)
        manifeste["fichiers"].append({"fichier": nom, "sha256": rs.sha256(dst / nom), "octets": p.stat().st_size})
        print(f"  figé   : {nom}  {manifeste['fichiers'][-1]['sha256'][:16]}…")
    (dst / "MANIFESTE.json").write_text(json.dumps(manifeste, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Copie figée dans {dst} (MANIFESTE.json).")


def _verifier_manifeste(copie: Path) -> list[dict]:
    mf = copie / "MANIFESTE.json"
    if not mf.exists():
        sys.exit("MANIFESTE.json absent : figez d'abord les sources (commande figer).")
    manifeste = json.loads(mf.read_text(encoding="utf-8"))
    for f in manifeste["fichiers"]:
        if rs.sha256(copie / f["fichier"]) != f["sha256"]:
            sys.exit(f"{f['fichier']} a changé depuis le gel (empreinte différente) : import refusé.")
    return manifeste["fichiers"]


async def analyser(args) -> None:
    copie = Path(args.copie)
    _verifier_manifeste(copie)
    mapping = _mapping(args.mapping)
    sources = rs.charger_sources(copie, mapping)
    async with AsyncSessionLocal() as db:
        rapport = await rs.analyser(db, sources, mapping)
    print(json.dumps({k: v for k, v in rapport.items() if k != "a_faire_correspondre"}, indent=2, ensure_ascii=False, default=str))
    a_faire = rapport["a_faire_correspondre"]
    print(f"\nCodes sans correspondance : {len(a_faire['tuteur'])} tuteur(s), {len(a_faire['eleve'])} élève(s).")
    if args.sortie:
        with open(args.sortie, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["type", "code_source", "libelle_source", "cible_id", "suggestion_cible_id", "suggestion_libelle",
                        "suggestion_raison", "commentaire"])
            for type_, items in a_faire.items():
                for it in items:
                    s = it["suggestions"][0] if len(it["suggestions"]) == 1 else None
                    w.writerow([type_, it["code_source"], it["libelle_source"], "", s["cible_id"] if s else "",
                                s["libelle"] if s else ("plusieurs candidats" if it["suggestions"] else ""),
                                s["raison"] if s else "", ""])
        print(f"Fichier à valider : {args.sortie} (remplir cible_id ; une suggestion n'est jamais appliquée seule).")


async def correspondances(args) -> None:
    ok = err = 0
    async with AsyncSessionLocal() as db:
        with open(args.fichier, encoding="utf-8-sig") as fh:
            for n, l in enumerate(csv.DictReader(fh, delimiter=";"), start=2):
                cible = (l.get("cible_id") or "").strip()
                if not cible:
                    continue
                try:
                    db.add(CorrespondanceReprise(type=l["type"].strip(), code_source=l["code_source"].strip(),
                                                 libelle_source=l.get("libelle_source"), cible_id=uuid.UUID(cible),
                                                 valide_par=args.par, commentaire=l.get("commentaire")))
                    await db.flush()
                    ok += 1
                except Exception as e:
                    await db.rollback()
                    err += 1
                    print(f"  ligne {n} : {e}")
        await db.commit()
    print(f"{ok} correspondance(s) validée(s), {err} erreur(s).")


async def importer(args) -> None:
    copie = Path(args.copie)
    fichiers = _verifier_manifeste(copie)
    mapping = _mapping(args.mapping)
    sources = rs.charger_sources(copie, mapping)
    async with AsyncSessionLocal() as db:
        lot = LotReprise(nom=args.nom, session_id=uuid.UUID(args.campagne) if args.campagne else None,
                         sources=fichiers, statut="analyse", cree_par=args.par,
                         commentaire=f"Import depuis {copie}" + (" (simulation)" if args.simulation else ""))
        db.add(lot)
        await db.flush()
        resultat = await rs.importer(db, lot, sources, mapping)
        print(json.dumps(resultat["compteurs"], indent=2, ensure_ascii=False))
        total = resultat["reconciliation"]["rapports"]["total"]
        print("\nRéconciliation (total) :", json.dumps(total, ensure_ascii=False))
        print("Écarts expliqués :", resultat["reconciliation"]["toutes_lignes_expliquees"])
        if args.rapport:
            Path(args.rapport).write_text(json.dumps(resultat, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        if args.simulation:
            await db.rollback()
            print("\nSimulation : rien n'a été enregistré.")
        else:
            await db.commit()
            print(f"\nLot {lot.id} enregistré.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("figer"); a.add_argument("--sources", required=True); a.add_argument("--copie", required=True)
    a = sp.add_parser("analyser"); a.add_argument("--copie", required=True); a.add_argument("--mapping"); a.add_argument("--sortie")
    a = sp.add_parser("correspondances"); a.add_argument("--fichier", required=True); a.add_argument("--par", required=True)
    a = sp.add_parser("importer")
    for opt in ("--copie", "--nom", "--par"):
        a.add_argument(opt, required=True)
    a.add_argument("--campagne"); a.add_argument("--mapping"); a.add_argument("--rapport")
    a.add_argument("--simulation", action="store_true")
    args = p.parse_args()
    if args.cmd == "figer":
        figer(args)
    else:
        asyncio.run({"analyser": analyser, "correspondances": correspondances, "importer": importer}[args.cmd](args))


if __name__ == "__main__":
    main()
