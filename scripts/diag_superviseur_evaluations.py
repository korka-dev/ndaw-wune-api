#!/usr/bin/env python3
"""
Diagnostic — Pourquoi un superviseur ne voit-il aucun sujet d'évaluation ?
==========================================================================

Rejoue exactement la logique de GET /app/supervisor/evaluation-sujets et
affiche, étape par étape, ce qui bloque. Trois causes possibles, que ce
script distingue sans ambiguïté :

  1. Aucun enseignant supervisé (ni assignation explicite, ni école de repli)
  2. Langue : aucun sujet ne correspond à la langue d'enseignement
  3. Aucun tirage : les sujets existent et la langue correspond, mais aucun
     élève des classes supervisées n'a été tiré au sort — le endpoint exclut
     alors le sujet (`if eleves_app:`). C'est une cause INDÉPENDANTE de la
     langue, que la correction des langues ne résout pas.

Usage :
    python scripts/diag_superviseur_evaluations.py                 # tous les superviseurs
    python scripts/diag_superviseur_evaluations.py --langue pulaar # filtre sur une langue
    python scripts/diag_superviseur_evaluations.py --tel 771234567 # un superviseur précis
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_DEFAULT_DB = "postgresql+asyncpg://ared_user:ared_secret@localhost:5432/ared_ndawune"
if not os.path.exists("/.dockerenv") and (
    "DATABASE_URL" not in os.environ
    or "://db:" in os.environ.get("DATABASE_URL", "")
):
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", _DEFAULT_DB).replace(
        "@db:", "@localhost:"
    )
    if os.environ["DATABASE_URL"].startswith("postgresql+asyncpg://$"):
        os.environ["DATABASE_URL"] = _DEFAULT_DB

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.langue import canonical_langue, langue_matches
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.school import School
from app.models.user import User, UserRole
from app.services.supervisor_service import (
    get_supervised_teacher_ids,
    resolve_supervisor_langue,
)

G = "\033[0;32m"; R = "\033[0;31m"; Y = "\033[1;33m"; C = "\033[0;36m"; B = "\033[1m"; N = "\033[0m"


async def diag_un(db, sup: User, sujets: list[EvaluationSujet]) -> str:
    ecole = sup.school.name if sup.school else "— aucune —"
    langue_brute = sup.school.langue if sup.school else None
    print(f"\n{B}{C}▶ {sup.name}{N}  ({sup.phone or sup.email or sup.id})")
    print(f"  École            : {ecole}")
    print(f"  Langue (brute)   : {langue_brute!r}")

    # ── 1. Enseignants supervisés ────────────────────────────────────────
    explicite = bool(sup.classes)
    teacher_ids = await get_supervised_teacher_ids(db, sup)
    origine = "assignation explicite" if explicite else "repli sur l'école"
    print(f"  Enseignants      : {len(teacher_ids)} ({origine})")
    if not teacher_ids:
        print(f"  {R}➜ BLOCage 1 : aucun enseignant supervisé.{N}")
        return "sans_enseignant"

    # ── 2. Paires (école, classe) ────────────────────────────────────────
    teachers = (await db.execute(select(User).where(User.id.in_(teacher_ids)))).scalars().all()
    pairs = [(t.school_id, cls) for t in teachers if t.school_id and t.classes for cls in t.classes]
    print(f"  Classes couvertes: {len(pairs)}")
    if not pairs:
        print(f"  {R}➜ BLOCAGE 1b : les enseignants n'ont ni école ni classes renseignées.{N}")
        return "sans_classe"

    # ── 3. Langue ────────────────────────────────────────────────────────
    langue = await resolve_supervisor_langue(db, sup)
    print(f"  Langue (canon.)  : {langue!r}")
    if langue is None:
        print(f"  {Y}➜ Langue indéterminable : TOUS les sujets sont proposés (pas de filtre).{N}")
        retenus = sujets
    else:
        retenus = [s for s in sujets if not s.langue or langue_matches(s.langue, langue)]
    print(f"  Sujets après filtre langue : {len(retenus)} / {len(sujets)}")
    if not retenus:
        print(f"  {R}➜ BLOCAGE 2 : aucun sujet dans cette langue.{N}")
        print(f"     Langues des sujets existants : "
              f"{sorted({(s.langue or 'toutes') for s in sujets})}")
        return "langue"

    # ── 4. Tirages ───────────────────────────────────────────────────────
    total_eleves = 0
    sujets_visibles = 0
    for sujet in retenus:
        tirages = (await db.execute(
            select(EvaluationTirage)
            .options(selectinload(EvaluationTirage.eleve))
            .where(EvaluationTirage.sujet_id == sujet.id)
        )).scalars().all()
        n = sum(
            1 for t in tirages
            if t.eleve is not None
            and any(t.eleve.school_id == sid and t.eleve.classe == cls for sid, cls in pairs)
        )
        total_eleves += n
        if n:
            sujets_visibles += 1

    print(f"  Sujets réellement visibles : {sujets_visibles} ({total_eleves} élèves tirés)")
    if sujets_visibles == 0:
        print(f"  {R}➜ BLOCAGE 3 : la langue correspond, mais AUCUN élève des classes")
        print(f"     supervisées n'a été tiré au sort. Lancez le tirage pour ces écoles")
        print(f"     depuis le dashboard — la correction des langues n'y change rien.{N}")
        return "sans_tirage"

    print(f"  {G}➜ OK : ce superviseur doit voir {sujets_visibles} sujet(s).{N}")
    return "ok"


async def main(langue_filtre: str | None, tel: str | None) -> None:
    async with AsyncSessionLocal() as db:
        q = select(User).where(User.role == UserRole.superviseur).order_by(User.name)
        if tel:
            q = q.where((User.phone == tel) | (User.email == tel))
        sups = (await db.execute(q)).scalars().all()

        sujets = (await db.execute(select(EvaluationSujet))).scalars().all()
        print(f"{B}Sujets d'évaluation en base : {len(sujets)}{N}")
        for s in sujets:
            print(f"  · {s.titre[:45]:45} langue={s.langue!r} → canon={canonical_langue(s.langue)!r}")

        print(f"\n{B}Langues des écoles :{N}")
        rows = (await db.execute(select(School.langue).distinct())).scalars().all()
        for l in sorted({str(x) for x in rows if x}):
            print(f"  · {l!r} → canon={canonical_langue(l)!r}")

        if langue_filtre:
            cible = canonical_langue(langue_filtre)
            sups = [
                s for s in sups
                if s.school and canonical_langue(s.school.langue) == cible
            ]
            print(f"\n{B}Superviseurs filtrés sur « {langue_filtre} » : {len(sups)}{N}")

        resultats: dict[str, int] = {}
        for sup in sups:
            r = await diag_un(db, sup, sujets)
            resultats[r] = resultats.get(r, 0) + 1

        print(f"\n{B}══ Résumé ══{N}")
        libelles = {
            "ok":              f"{G}Voient bien leurs sujets{N}",
            "langue":          f"{R}Bloqués par la langue{N}",
            "sans_tirage":     f"{R}Bloqués : aucun tirage pour leurs élèves{N}",
            "sans_enseignant": f"{R}Bloqués : aucun enseignant supervisé{N}",
            "sans_classe":     f"{R}Bloqués : enseignants sans école/classes{N}",
        }
        for k, v in sorted(resultats.items(), key=lambda kv: -kv[1]):
            print(f"  {v:3} — {libelles.get(k, k)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--langue", help="ne diagnostiquer que les superviseurs de cette langue")
    p.add_argument("--tel", help="téléphone ou email d'un superviseur précis")
    a = p.parse_args()
    asyncio.run(main(a.langue, a.tel))
