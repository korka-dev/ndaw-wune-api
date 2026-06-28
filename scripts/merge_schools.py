#!/usr/bin/env python3
"""
Fusionne les écoles en doublon dans la base NDAW WUNE.
======================================================
Problème : l'ancien import a créé des écoles sans préfixe "EE "
(ex: "BAMBEY SERERE 1"), le nouveau avec (ex: "EE BAMBEY SERERE 1").
Résultat : les enseignants et superviseurs pointent vers des écoles différentes.

Ce script :
  1. Identifie les paires (ancienne école sans code ↔ nouvelle école avec code)
  2. Migre enseignants, élèves et classes de l'ancienne vers la nouvelle
  3. Supprime les anciennes écoles orphelines

Usage (Docker) :
    docker exec ndaw-wune-backend-1 python scripts/merge_schools.py --dry-run
    docker exec ndaw-wune-backend-1 python scripts/merge_schools.py
"""

import asyncio
import os
import sys
from pathlib import Path
from difflib import SequenceMatcher

from sqlalchemy import select, update, delete, func

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_DEFAULT_DB = "postgresql+asyncpg://ared_user:ared_secret@localhost:5432/ared_ndawune"
if not os.path.exists("/.dockerenv"):
    raw = os.environ.get("DATABASE_URL", _DEFAULT_DB)
    if "://db:" in raw:
        raw = raw.replace("@db:", "@localhost:")
    if raw.startswith("postgresql+asyncpg://$"):
        raw = _DEFAULT_DB
    os.environ["DATABASE_URL"] = raw

from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.user import User, UserRole


def normalize(name: str) -> str:
    """Normalise un nom d'école pour comparaison."""
    n = name.strip().upper()
    for prefix in ("EE ", "EE. ", "ECOLE ", "ÉCOLE "):
        if n.startswith(prefix):
            n = n[len(prefix):]
    n = n.replace("  ", " ")
    return n


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


async def run(dry_run: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        # Charger toutes les écoles
        res = await session.execute(select(School))
        all_schools = list(res.scalars())

        schools_with_code = [s for s in all_schools if s.code_ecole is not None]
        schools_no_code = [s for s in all_schools if s.code_ecole is None]

        print(f"Écoles avec code_ecole : {len(schools_with_code)}")
        print(f"Écoles sans code_ecole : {len(schools_no_code)}")

        if not schools_no_code:
            print("\n✅ Aucune école orpheline — rien à fusionner.")
            return

        # ── Trouver les correspondances ──────────────────────────────────
        matches: list[tuple[School, School, float]] = []  # (old, new, score)
        unmatched: list[School] = []

        for old in schools_no_code:
            old_norm = normalize(old.name)
            best_match = None
            best_score = 0.0

            for new in schools_with_code:
                new_norm = normalize(new.name)

                # Match exact après normalisation
                if old_norm == new_norm:
                    best_match = new
                    best_score = 1.0
                    break

                # Match par similarité
                score = similarity(old_norm, new_norm)
                if score > best_score:
                    best_score = score
                    best_match = new

            if best_match and best_score >= 0.75:
                matches.append((old, best_match, best_score))
            else:
                unmatched.append(old)

        # ── Afficher les correspondances trouvées ────────────────────────
        print(f"\n{'─' * 70}")
        print(f"  {len(matches)} correspondances trouvées :")
        print(f"{'─' * 70}")

        for old, new, score in sorted(matches, key=lambda x: x[2], reverse=True):
            flag = "✓" if score == 1.0 else f"~{score:.0%}"
            print(f"  {flag:>4}  {old.name:<40} → {new.name} (code={new.code_ecole})")

        if unmatched:
            print(f"\n  ⚠ {len(unmatched)} écoles sans correspondance :")
            for s in unmatched:
                print(f"       {s.name} (id={s.id})")

        # ── Compter ce qui sera migré ────────────────────────────────────
        old_ids = [old.id for old, _, _ in matches]

        if old_ids:
            teacher_count = (await session.execute(
                select(func.count()).where(User.school_id.in_(old_ids), User.role == UserRole.enseignant)
            )).scalar()
            eleve_count = (await session.execute(
                select(func.count()).where(Eleve.school_id.in_(old_ids))
            )).scalar()
            classe_count = (await session.execute(
                select(func.count()).where(SchoolClasse.school_id.in_(old_ids))
            )).scalar()

            print(f"\n  À migrer : {teacher_count} enseignants, {eleve_count} élèves, {classe_count} classes")

        if dry_run:
            print(f"\n🔒 Mode dry-run — aucune modification.")
            return

        # ── Exécuter la fusion ───────────────────────────────────────────
        print(f"\n{'─' * 70}")
        print("  Fusion en cours…")
        print(f"{'─' * 70}")

        migrated_teachers = 0
        migrated_eleves = 0
        migrated_classes = 0
        deleted_schools = 0

        for old, new, score in matches:
            # Migrer les enseignants
            t_result = await session.execute(
                update(User)
                .where(User.school_id == old.id)
                .values(school_id=new.id)
            )
            migrated_teachers += t_result.rowcount

            # Migrer les élèves — supprimer les doublons, déplacer les uniques
            old_eleves = (await session.execute(
                select(Eleve).where(Eleve.school_id == old.id)
            )).scalars().all()

            new_eleves_res = (await session.execute(
                select(Eleve.classe, Eleve.nom, Eleve.prenom)
                .where(Eleve.school_id == new.id)
            )).all()
            new_eleve_keys = {(r[0], r[1], r[2] or "") for r in new_eleves_res}

            for eleve in old_eleves:
                key = (eleve.classe, eleve.nom, eleve.prenom or "")
                if key in new_eleve_keys:
                    await session.execute(
                        delete(Eleve).where(Eleve.id == eleve.id)
                    )
                else:
                    eleve.school_id = new.id
                    new_eleve_keys.add(key)
                    migrated_eleves += 1

            # Migrer les classes — supprimer les doublons, déplacer les uniques
            old_classes = (await session.execute(
                select(SchoolClasse).where(SchoolClasse.school_id == old.id)
            )).scalars().all()

            existing_classes = (await session.execute(
                select(SchoolClasse.name).where(SchoolClasse.school_id == new.id)
            )).scalars().all()
            existing_names = set(existing_classes)

            for sc in old_classes:
                if sc.name in existing_names:
                    await session.execute(
                        delete(SchoolClasse).where(SchoolClasse.id == sc.id)
                    )
                else:
                    sc.school_id = new.id
                    migrated_classes += 1

            await session.flush()

            # Supprimer l'ancienne école
            await session.execute(delete(School).where(School.id == old.id))
            deleted_schools += 1

        await session.flush()

        # ── Maintenant relier superviseurs aux enseignants ───────────────
        print("\n  Liaison superviseurs ↔ enseignants…")

        teachers_res = await session.execute(
            select(User).where(User.role == UserRole.enseignant)
        )
        teachers_by_school: dict = {}
        for t in teachers_res.scalars():
            if t.school_id:
                teachers_by_school.setdefault(t.school_id, []).append(str(t.id))

        sups_res = await session.execute(
            select(User).where(User.role == UserRole.superviseur)
        )
        linked = 0
        for sup in sups_res.scalars():
            teacher_ids = teachers_by_school.get(sup.school_id, [])
            if not teacher_ids:
                continue
            existing_ids = set(sup.classes or [])
            merged = sorted(existing_ids | set(teacher_ids))
            if set(sup.classes or []) != set(merged):
                sup.classes = merged
                linked += 1

        await session.commit()

        print(f"\n{'═' * 70}")
        print(f"  ✅ Fusion terminée !")
        print(f"     {migrated_teachers} enseignants migrés")
        print(f"     {migrated_eleves} élèves migrés")
        print(f"     {migrated_classes} classes migrées")
        print(f"     {deleted_schools} écoles supprimées")
        print(f"     {linked} superviseurs liés à leurs enseignants")
        print(f"{'═' * 70}")

        # Vérification finale
        total = (await session.execute(select(func.count()).select_from(School))).scalar()
        print(f"\n  Écoles restantes en DB : {total}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(run(dry_run=dry))
