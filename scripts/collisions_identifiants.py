"""Liste les comptes en collision d'identifiant (rapport V2, APP-35).

À lancer AVANT la migration t8u9v0w1x2y3 : deux comptes dont l'e-mail ne
diffère que par la casse ou des espaces empêchent la création de l'index
d'unicité insensible à la casse. Rien n'est modifié : l'arbitrage (quel compte
garde l'e-mail) revient à l'administrateur.

    python -m scripts.collisions_identifiants
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

REQUETES = {
    "e-mail": (
        "SELECT lower(btrim(email)) AS cle, id, name, role, status, email "
        "FROM users WHERE email IS NOT NULL AND btrim(email) <> '' AND lower(btrim(email)) IN ("
        "  SELECT lower(btrim(email)) FROM users WHERE email IS NOT NULL "
        "  GROUP BY lower(btrim(email)) HAVING count(*) > 1) ORDER BY cle, created_at"
    ),
}


async def main() -> None:
    async with AsyncSessionLocal() as s:
        total = 0
        for libelle, sql in REQUETES.items():
            rows = (await s.execute(text(sql))).all()
            total += len(rows)
            courant = None
            for cle, uid, name, role, status, brut in rows:
                if cle != courant:
                    print(f"\n[{libelle}] {cle}")
                    courant = cle
                print(f"   - {uid}  {name!r}  rôle={role}  statut={status}  saisi={brut!r}")
        print("\nAucune collision." if total == 0 else f"\n{total} compte(s) à arbitrer, aucune modification faite.")


if __name__ == "__main__":
    asyncio.run(main())
