"""Liste les fiches école de même nom normalisé (rapport V2, APP-23).

Pour chaque groupe : les fiches, leur code / commune / IEF, le nombre
d'élèves et de comptes rattachés, et si l'application les rapproche
(« fusionnées à l'affichage ») ou les isole (élément géographique
contradictoire). Rien n'est modifié : l'arbitrage revient à l'ARED, puis à
l'administrateur (corriger code / commune, ou réaffecter les élèves).

    python -m scripts.doublons_ecoles
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.school import School
from app.models.user import User
from app.services.school_service import _compatible, normalize_school_name


async def main() -> None:
    async with AsyncSessionLocal() as s:
        schools = (await s.execute(select(School))).scalars().all()
        nb_eleves = dict((await s.execute(select(Eleve.school_id, func.count()).group_by(Eleve.school_id))).all())
        nb_comptes = dict((await s.execute(select(User.school_id, func.count()).group_by(User.school_id))).all())
    groupes: dict[str, list[School]] = defaultdict(list)
    for sc in schools:
        groupes[normalize_school_name(sc.name or "")].append(sc)
    n = 0
    for nom, fiches in sorted(groupes.items()):
        if len(fiches) < 2:
            continue
        n += 1
        print(f"\n« {nom} » — {len(fiches)} fiches")
        for f in fiches:
            lies = [o.code_ecole for o in fiches if o.id != f.id and _compatible(f, o)]
            etat = "rapprochée" if lies else "isolée"
            print(f"   - {f.id}  code={f.code_ecole}  commune={f.city!r}  IEF={f.region!r}  "
                  f"élèves={nb_eleves.get(f.id, 0)}  comptes={nb_comptes.get(f.id, 0)}  "
                  f"{'archivée ' if f.archived_at else ''}[{etat}]")
    print("\nAucun homonyme." if n == 0 else f"\n{n} groupe(s) à arbitrer. Aucune modification faite.")


if __name__ == "__main__":
    asyncio.run(main())
