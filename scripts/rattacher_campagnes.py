"""Rattachement des données antérieures à leur campagne (rapport V2, APP-22).

Les rapports journaliers et pointages superviseur reçus avant le lot L3 n'ont
pas de `session_id`. Ce script ne les rattache que lorsqu'il n'y a **aucune
ambiguïté** : la date tombe dans les dates d'une seule campagne à laquelle
le tuteur est affecté. Les cas ambigus ou sans campagne sont listés, jamais
devinés. Aperçu par défaut ; `--appliquer` écrit et journalise.

    docker compose exec -T backend python -m scripts.rattacher_campagnes
    docker compose exec -T backend python -m scripts.rattacher_campagnes --appliquer [--detail cas.csv]
"""
from __future__ import annotations

import argparse
import asyncio
import csv

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services.audit_service import journaliser

TABLES = {
    "rapports_journalier": "date_rapport",
    "supervisor_presence_checks": "date_jour",
}

_CANDIDATS = """
WITH cand AS (
    SELECT t.id, ps.id AS sid
    FROM {table} t
    JOIN program_sessions ps ON t.{col} BETWEEN ps.date_debut AND ps.date_fin
    JOIN teacher_sessions ts ON ts.session_id = ps.id AND ts.teacher_id = t.teacher_id
    WHERE t.session_id IS NULL
), agg AS (
    SELECT id, count(*) AS n, min(sid::text) AS sid FROM cand GROUP BY id
)
SELECT t.id::text AS id, t.teacher_id::text AS teacher_id, t.{col}::text AS date,
       coalesce(a.n, 0) AS n, a.sid
FROM {table} t LEFT JOIN agg a ON a.id = t.id
WHERE t.session_id IS NULL
"""


async def traiter(appliquer: bool, detail: str | None) -> dict:
    bilan, cas = {}, []
    async with AsyncSessionLocal() as db:
        for table, col in TABLES.items():
            lignes = (await db.execute(text(_CANDIDATS.format(table=table, col=col)))).mappings().all()
            uniques = [l for l in lignes if l["n"] == 1]
            ambigus = [l for l in lignes if l["n"] > 1]
            sans = [l for l in lignes if l["n"] == 0]
            bilan[table] = {"sans_campagne": len(lignes), "rattachables": len(uniques),
                            "ambigus": len(ambigus), "aucune_campagne": len(sans)}
            cas += [{"table": table, "id": l["id"], "teacher_id": l["teacher_id"], "date": l["date"],
                     "statut": "ambigu" if l["n"] > 1 else "aucune_campagne"} for l in ambigus + sans]
            if appliquer and uniques:
                for i in range(0, len(uniques), 1000):
                    lot = uniques[i:i + 1000]
                    await db.execute(
                        text(f"UPDATE {table} SET session_id = CAST(v.sid AS uuid) "
                             f"FROM (SELECT unnest(CAST(:ids AS text[])) AS id, unnest(CAST(:sids AS text[])) AS sid) v "
                             f"WHERE {table}.id = CAST(v.id AS uuid) AND {table}.session_id IS NULL"),
                        {"ids": [l["id"] for l in lot], "sids": [l["sid"] for l in lot]},
                    )
        if appliquer:
            journaliser(db, acteur=None, action="parametrage", entity="Rattachement aux campagnes",
                        description="Rattachement des données antérieures : " + ", ".join(
                            f"{t} {b['rattachables']} rattachée(s), {b['ambigus']} ambiguë(s), "
                            f"{b['aucune_campagne']} sans campagne" for t, b in bilan.items()),
                        path="scripts/rattacher_campagnes")
            await db.commit()
    if detail and cas:
        with open(detail, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["table", "id", "teacher_id", "date", "statut"], delimiter=";")
            w.writeheader()
            w.writerows(cas)
    return bilan


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--appliquer", action="store_true", help="écrire les rattachements (sinon aperçu)")
    p.add_argument("--detail", help="CSV des cas ambigus ou sans campagne (identifiants seulement)")
    a = p.parse_args()
    bilan = asyncio.run(traiter(a.appliquer, a.detail))
    for t, b in bilan.items():
        print(f"{t:28} sans campagne {b['sans_campagne']:6} · rattachables {b['rattachables']:6} · "
              f"ambigus {b['ambigus']:5} · aucune campagne {b['aucune_campagne']:5}")
    print("Appliqué." if a.appliquer else "Aperçu seul : relancer avec --appliquer.")


if __name__ == "__main__":
    main()
