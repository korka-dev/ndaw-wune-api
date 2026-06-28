#!/usr/bin/env python3
"""
Nettoyage qualité données — Table eleves
=========================================
Corrige :
  1. Données de test (orphelins sans école) → suppression
  2. Noms suspects (nom="1","2","3"…) → réattribution nom/prénom
  3. Classes mal nommées (CE1-A, CP-GENRE, CP C) → normalisation
  4. Doublons cross-classe :
     - Les deux ont un code_eleve différent → homonymes, on garde les deux
     - Un seul a un code → on supprime celui sans code (ancien import)
     - Aucun n'a de code → on garde le plus ancien
  5. Doublons exacts restants → on garde celui avec code_eleve

Usage :
    python scripts/cleanup_eleves.py              (dry-run par défaut)
    python scripts/cleanup_eleves.py --apply       (applique les changements)
"""

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

from sqlalchemy import text
from app.core.database import AsyncSessionLocal

DRY_RUN = "--apply" not in sys.argv


async def run():
    mode = "DRY-RUN (aucune modification)" if DRY_RUN else "APPLICATION RÉELLE"
    print(f"\n{'='*60}")
    print(f"  Nettoyage qualité données élèves — {mode}")
    print(f"{'='*60}\n")

    async with AsyncSessionLocal() as s:
        total_before = (await s.execute(text("SELECT COUNT(*) FROM eleves"))).scalar()
        print(f"Total élèves avant nettoyage : {total_before}\n")

        deleted = 0
        fixed = 0

        # ── 1. Supprimer les données de test (orphelins sans école) ──────────
        print("── [1/5] Données de test (orphelins sans école) ──────────")
        rows = (await s.execute(text(
            "SELECT id, nom, prenom, classe FROM eleves WHERE school_id IS NULL"
        ))).fetchall()
        print(f"  Trouvé : {len(rows)} élèves orphelins")
        for r in rows:
            print(f"    → {r[1]} {r[2]} (classe={r[3]})")

        if rows and not DRY_RUN:
            result = await s.execute(text("DELETE FROM eleves WHERE school_id IS NULL"))
            deleted += result.rowcount
            print(f"  ✓ {result.rowcount} supprimés")
        elif rows:
            deleted += len(rows)
            print(f"  (serait supprimé : {len(rows)})")

        # ── 2. Corriger les noms suspects (nom = chiffre ou lettre seule) ────
        print("\n── [2/5] Noms suspects (nom = chiffre/lettre) ─────────")
        rows = (await s.execute(text("""
            SELECT e.id, e.nom, e.prenom, e.school_id, e.classe FROM eleves e
            WHERE e.nom ~ '^[0-9]+$' OR (LENGTH(e.nom) = 1 AND e.nom ~ '^[A-Za-z]$')
            ORDER BY e.prenom
        """))).fetchall()
        print(f"  Trouvé : {len(rows)} élèves avec nom suspect")

        deleted_as_dup = 0
        for r in rows:
            old_nom = r[1]
            old_prenom = r[2] or ""
            school_id = r[3]
            classe = r[4]
            parts = old_prenom.strip().split()
            if len(parts) >= 2:
                new_nom = parts[-1]
                new_prenom = " ".join(parts[:-1])
            elif len(parts) == 1:
                new_nom = parts[0]
                new_prenom = None
            else:
                print(f"    ⚠ Impossible de corriger id={r[0]} nom='{old_nom}' prenom='{old_prenom}'")
                continue

            conflict = (await s.execute(text("""
                SELECT id FROM eleves
                WHERE school_id = :sid AND classe = :cls AND nom = :nom
                  AND COALESCE(prenom, '') = COALESCE(:prenom, '')
                  AND id != :eid
            """), {"sid": school_id, "cls": classe, "nom": new_nom,
                   "prenom": new_prenom, "eid": r[0]})).fetchone()

            if conflict:
                print(f"    → '{old_prenom} {old_nom}' doublon masqué → suppression")
                if not DRY_RUN:
                    await s.execute(text("DELETE FROM eleves WHERE id = :id"), {"id": r[0]})
                deleted += 1
                deleted_as_dup += 1
            else:
                print(f"    → '{old_prenom} {old_nom}' => nom='{new_nom}' prenom='{new_prenom}'")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE eleves SET nom = :nom, prenom = :prenom WHERE id = :id"
                    ), {"nom": new_nom, "prenom": new_prenom, "id": r[0]})
                fixed += 1

        if not DRY_RUN:
            await s.flush()
        print(f"  {'✓' if not DRY_RUN else '(serait)'} : {fixed} noms corrigés, {deleted_as_dup} doublons masqués supprimés")

        # ── 3. Normaliser les classes ────────────────────────────────────────
        print("\n── [3/5] Normalisation des classes ─────────────────────")
        class_fixes = {
            "CE1-A": "CE1 A",
            "CP-GENRE": "CP",
            "CP C": "CP",
        }
        class_fixed = 0
        for old, new in class_fixes.items():
            cnt = (await s.execute(text(
                "SELECT COUNT(*) FROM eleves WHERE classe = :old"
            ), {"old": old})).scalar()
            if cnt > 0:
                print(f"    → '{old}' => '{new}' ({cnt} élèves)")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE eleves SET classe = :new WHERE classe = :old"
                    ), {"old": old, "new": new})
                class_fixed += cnt

        if class_fixed:
            if not DRY_RUN:
                await s.flush()
            print(f"  {'✓' if not DRY_RUN else '(serait corrigé)'} : {class_fixed} classes normalisées")
        else:
            print("  Rien à corriger.")

        # ── 4. Doublons cross-classe (même nom+prénom, même école, classes diff) ──
        print("\n── [4/5] Doublons cross-classe ─────────────────────────")
        pairs = (await s.execute(text("""
            SELECT e1.id as id1, e2.id as id2,
                   e1.nom, COALESCE(e1.prenom,''),
                   e1.classe, e2.classe,
                   e1.code_eleve, e2.code_eleve,
                   e1.created_at, e2.created_at
            FROM eleves e1
            JOIN eleves e2 ON e1.school_id = e2.school_id
                AND e1.nom = e2.nom
                AND COALESCE(e1.prenom,'') = COALESCE(e2.prenom,'')
                AND e1.id < e2.id
                AND e1.classe != e2.classe
        """))).fetchall()

        ids_to_delete = set()
        homonymes = 0

        for p in pairs:
            id1, id2, nom, prenom, c1, c2, code1, code2, t1, t2 = p

            if code1 and code2 and code1 != code2:
                homonymes += 1
                continue

            if code1 and not code2:
                ids_to_delete.add(id2)
            elif code2 and not code1:
                ids_to_delete.add(id1)
            elif code1 and code2 and code1 == code2:
                ids_to_delete.add(id2 if t1 <= t2 else id1)
            else:
                ids_to_delete.add(id2 if t1 <= t2 else id1)

        print(f"  Homonymes légitimes (codes différents, gardés) : {homonymes}")
        print(f"  Vrais doublons à supprimer : {len(ids_to_delete)}")

        if ids_to_delete and not DRY_RUN:
            for uid in ids_to_delete:
                await s.execute(text("DELETE FROM eleves WHERE id = :id"), {"id": uid})
            deleted += len(ids_to_delete)
            print(f"  ✓ {len(ids_to_delete)} supprimés")
        elif ids_to_delete:
            deleted += len(ids_to_delete)
            print(f"  (serait supprimé : {len(ids_to_delete)})")

        # ── 5. Doublons exacts restants (même école, classe, nom, prénom) ────
        print("\n── [5/5] Doublons exacts restants ──────────────────────")
        exact_dups = (await s.execute(text("""
            SELECT id, nom, prenom, classe, code_eleve, created_at FROM (
                SELECT id, nom, prenom, classe, code_eleve, created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY school_id, classe, nom, COALESCE(prenom, '')
                           ORDER BY
                               CASE WHEN code_eleve IS NOT NULL THEN 0 ELSE 1 END,
                               created_at ASC
                       ) as rn
                FROM eleves
            ) ranked
            WHERE rn > 1
        """))).fetchall()

        print(f"  Trouvé : {len(exact_dups)} doublons exacts")
        for r in exact_dups[:10]:
            print(f"    → {r[2] or ''} {r[1]} classe={r[3]} code={r[4]}")

        if exact_dups and not DRY_RUN:
            dup_ids = [r[0] for r in exact_dups]
            for uid in dup_ids:
                await s.execute(text("DELETE FROM eleves WHERE id = :id"), {"id": uid})
            deleted += len(dup_ids)
            print(f"  ✓ {len(dup_ids)} supprimés")
        elif exact_dups:
            deleted += len(exact_dups)
            print(f"  (serait supprimé : {len(exact_dups)})")

        # ── Résumé ───────────────────────────────────────────────────────────
        if not DRY_RUN:
            await s.commit()
            total_after = (await s.execute(text("SELECT COUNT(*) FROM eleves"))).scalar()
            dups_after = (await s.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM eleves
                    GROUP BY school_id, classe, nom, COALESCE(prenom, '')
                    HAVING COUNT(*) > 1
                ) sub
            """))).scalar()

            print(f"\n{'='*60}")
            print(f"  ✅ Nettoyage terminé !")
            print(f"  Avant : {total_before} élèves")
            print(f"  Après : {total_after} élèves")
            print(f"  Supprimés : {deleted}")
            print(f"  Noms corrigés : {fixed}")
            print(f"  Classes normalisées : {class_fixed}")
            print(f"  Doublons restants : {dups_after}")
            print(f"{'='*60}\n")
        else:
            print(f"\n{'='*60}")
            print(f"  DRY-RUN — Résumé des actions prévues :")
            print(f"  Serait supprimé : {deleted}")
            print(f"  Serait corrigé (noms) : {fixed}")
            print(f"  Serait normalisé (classes) : {class_fixed}")
            print(f"\n  → Relancer avec --apply pour exécuter")
            print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run())
