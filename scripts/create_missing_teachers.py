#!/usr/bin/env python3
"""
Création des enseignants manquants
==================================
Pour chaque école ayant un superviseur mais 0 enseignant,
crée 1 enseignant par classe existante (SchoolClasse).

L'enseignant reçoit :
  - name = "Enseignant [classe]"  (provisoire — à renommer dans l'admin)
  - classes = [nom_classe]
  - niveau  = [niveau_classe]
  - school_id = l'école
  - mot de passe par défaut, must_change_password = True

Puis assigne automatiquement tous les enseignants créés au superviseur.

Usage :
    python scripts/create_missing_teachers.py              (dry-run)
    python scripts/create_missing_teachers.py --apply       (applique)
"""

import asyncio
import os
import sys
import uuid
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

from sqlalchemy import text
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password

DRY_RUN = "--apply" not in sys.argv
DEFAULT_PASSWORD = "NdawWune2025!"


async def run():
    mode = "DRY-RUN" if DRY_RUN else "APPLICATION"
    print(f"\n{'='*60}")
    print(f"  Création enseignants manquants — {mode}")
    print(f"{'='*60}\n")

    pwd_hash = hash_password(DEFAULT_PASSWORD)

    async with AsyncSessionLocal() as s:

        # Trouver les écoles avec superviseur mais 0 enseignant
        schools = (await s.execute(text("""
            SELECT DISTINCT s.id, s.name, s.code_ecole
            FROM schools s
            JOIN users u ON u.school_id = s.id AND u.role = 'superviseur'
            WHERE (SELECT COUNT(*) FROM users t WHERE t.school_id = s.id AND t.role = 'enseignant') = 0
              AND s.code_ecole IS NOT NULL
            ORDER BY s.code_ecole
        """))).fetchall()

        print(f"Écoles avec superviseur et 0 enseignant : {len(schools)}\n")

        total_created = 0
        total_assigned = 0

        for sch in schools:
            school_id, school_name, code_ecole = sch

            # Classes de cette école
            classes = (await s.execute(text("""
                SELECT name, niveau FROM school_classes WHERE school_id = :sid ORDER BY name
            """), {"sid": school_id})).fetchall()

            if not classes:
                print(f"  [{code_ecole}] {school_name} — 0 classes, rien à créer")
                continue

            print(f"  [{code_ecole}] {school_name} — {len(classes)} classes")

            teacher_ids = []
            for cls in classes:
                classe_name, niveau = cls
                teacher_name = f"Enseignant {classe_name}"
                teacher_id = uuid.uuid4()

                print(f"      + {teacher_name} (niveau={niveau})")

                if not DRY_RUN:
                    await s.execute(text("""
                        INSERT INTO users (id, name, email, phone, password_hash, role, status,
                                          must_change_password, school_id, niveau, classes,
                                          created_at, updated_at)
                        VALUES (:id, :name, NULL, NULL, :pwd, 'enseignant', 'actif',
                                TRUE, :sid, :niveau, :classes,
                                NOW(), NOW())
                    """), {
                        "id": teacher_id,
                        "name": teacher_name,
                        "pwd": pwd_hash,
                        "sid": school_id,
                        "niveau": [niveau] if niveau else None,
                        "classes": [classe_name],
                    })

                teacher_ids.append(str(teacher_id))
                total_created += 1

            # Assigner au superviseur
            sup = (await s.execute(text("""
                SELECT id, name, classes FROM users
                WHERE role = 'superviseur' AND school_id = :sid
            """), {"sid": school_id})).fetchone()

            if sup:
                existing = list(sup[2] or [])
                new_classes = list(set(existing + teacher_ids))
                print(f"      → Assigné à {sup[1]} ({len(teacher_ids)} enseignants)")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE users SET classes = :cls WHERE id = :id"
                    ), {"cls": new_classes, "id": sup[0]})
                total_assigned += 1

        if not DRY_RUN:
            await s.commit()

        print(f"\n{'='*60}")
        if not DRY_RUN:
            sups_with = (await s.execute(text("""
                SELECT COUNT(*) FROM users
                WHERE role = 'superviseur' AND classes IS NOT NULL AND array_length(classes, 1) > 0
            """))).scalar()
            total_sups = (await s.execute(text(
                "SELECT COUNT(*) FROM users WHERE role = 'superviseur'"
            ))).scalar()
            total_teachers = (await s.execute(text(
                "SELECT COUNT(*) FROM users WHERE role = 'enseignant'"
            ))).scalar()

            print(f"  ✅ Terminé !")
            print(f"  Enseignants créés : {total_created}")
            print(f"  Superviseurs assignés : {total_assigned}")
            print(f"  Total enseignants : {total_teachers}")
            print(f"  Superviseurs avec enseignants : {sups_with}/{total_sups}")
            print(f"  Mot de passe enseignants : {DEFAULT_PASSWORD}")
        else:
            print(f"  DRY-RUN — Actions prévues :")
            print(f"  Enseignants à créer : {total_created}")
            print(f"  Superviseurs à assigner : {total_assigned}")
            print(f"\n  → Relancer avec --apply pour exécuter")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run())
