"""Rapport périodique automatique (rapport V2, Partie II D9.4).

Produit, pour la campagne active (ou --campagne), sur la période écoulée :
- la liste de relance (acteurs sous l'attendu) ;
- la synthèse des soumissions, de l'assiduité, de la supervision et de l'appui.

Les fichiers nominatifs restent sur le serveur (--dossier, droits 600) ; le
webhook (ALERTE_WEBHOOK_URL) ne reçoit qu'une synthèse chiffrée, sans nom.

    python -m scripts.rapport_periodique --jours 7 --dossier /srv/nwn-rapports
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

from app.core.database import AsyncSessionLocal
from app.services import tableau_bord as tb


def _ecrire_csv(chemin: Path, lignes: list[dict]) -> None:
    if not lignes:
        chemin.write_text("", encoding="utf-8")
    else:
        with chemin.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(lignes[0].keys()), delimiter=";", extrasaction="ignore")
            w.writeheader()
            for l in lignes:
                w.writerow({k: (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in l.items()})
    chemin.chmod(0o600)


async def produire(campagne: str | None, jours: int, dossier: Path) -> dict:
    fin = date.today() - timedelta(days=1)
    f = tb.Filtres(session_id=uuid.UUID(campagne) if campagne else None, date_debut=fin - timedelta(days=jours - 1), date_fin=fin)
    async with AsyncSessionLocal() as db:
        vues = {
            "soumissions": await tb.soumissions(db, f), "relances": await tb.relances(db, f),
            "assiduite": await tb.assiduite(db, f), "supervision": await tb.supervision(db, f), "appui": await tb.appui(db, f),
        }
    cible = dossier / f"{f.date_debut:%Y%m%d}_{fin:%Y%m%d}"
    cible.mkdir(parents=True, exist_ok=True)
    synthese = {"periode": {"debut": str(f.date_debut), "fin": str(fin)}}
    for nom, v in vues.items():
        synthese[nom] = v["totaux"] if v["statut"] == "calcule" else {"non_calculable": v["raison"]}
        if v["statut"] == "calcule" and v.get("lignes"):
            _ecrire_csv(cible / f"{nom}.csv", v["lignes"])
    (cible / "synthese.json").write_text(json.dumps(synthese, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return synthese


def envoyer(synthese: dict) -> None:
    url = os.environ.get("ALERTE_WEBHOOK_URL")
    if not url:
        return
    s = synthese
    lignes = [f"NDAW WUNE ({os.environ.get('ALERTE_ENVIRONNEMENT', '')}) — rapport du {s['periode']['debut']} au {s['periode']['fin']}"]
    for nom in ("soumissions", "relances", "assiduite", "supervision", "appui"):
        lignes.append(f"• {nom} : " + ", ".join(f"{k}={v}" for k, v in s[nom].items()))
    data = json.dumps({"text": "\n".join(lignes)}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)  # noqa: S310 — URL fixée par l'exploitant


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--campagne")
    p.add_argument("--jours", type=int, default=7)
    p.add_argument("--dossier", default=os.environ.get("RAPPORTS_DIR", "/srv/nwn-rapports"))
    p.add_argument("--sans-webhook", action="store_true")
    a = p.parse_args()
    synthese = asyncio.run(produire(a.campagne, a.jours, Path(a.dossier)))
    print(json.dumps(synthese, indent=2, ensure_ascii=False, default=str))
    if not a.sans_webhook:
        envoyer(synthese)


if __name__ == "__main__":
    main()
