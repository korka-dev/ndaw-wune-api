#!/usr/bin/env python3
"""
Correction données superviseurs / écoles / enseignants
======================================================
Utilise le fichier Excel comme source de vérité (clé = code_ecole).

Corrige :
  1. Noms d'écoles : aligne sur les noms du fichier Excel
  2. Doublons d'écoles : renomme les écoles ayant le même nom mais des codes différents
  3. Champ director des écoles : aligne sur le superviseur (directeur Excel)
  4. Lien superviseur → école : vérifie et corrige school_id
  5. Auto-assignation enseignants : pour chaque superviseur sans enseignants assignés,
     assigne tous les enseignants de son école

Usage :
    python scripts/fix_superviseurs_ecoles.py              (dry-run)
    python scripts/fix_superviseurs_ecoles.py --apply       (applique)
"""

import asyncio
import os
import sys
from pathlib import Path

import openpyxl

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

XLSX_PATH = ROOT / "ListeDesELevesPricipauxRemplacement.xlsx"


def read_excel_schools() -> dict[int, dict]:
    """Lit l'Excel et retourne {code_ecole: {name, director, ief, commune}}"""
    wb = openpyxl.load_workbook(str(XLSX_PATH), data_only=True, read_only=True)
    ws = wb.active
    schools: dict[int, dict] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if len(row) < 12:
            continue
        code_ecole = row[5]
        ecole = row[6]
        directeur = row[4]
        ief = row[1]
        commune = row[3]
        if code_ecole is None or ecole is None:
            continue
        code = int(str(code_ecole).strip())
        if code not in schools:
            schools[code] = {
                "name": str(ecole).strip(),
                "director": str(directeur).strip() if directeur else None,
                "region": str(ief).strip() if ief else None,
                "city": str(commune).strip() if commune else None,
            }
    wb.close()
    return schools


async def run():
    mode = "DRY-RUN" if DRY_RUN else "APPLICATION"
    print(f"\n{'='*60}")
    print(f"  Correction superviseurs / écoles / enseignants — {mode}")
    print(f"{'='*60}\n")

    excel_schools = read_excel_schools()
    print(f"Écoles dans l'Excel : {len(excel_schools)}\n")

    async with AsyncSessionLocal() as s:

        # ── 1. Corriger les noms d'écoles ─────────────────────────────────
        print("── [1/5] Noms d'écoles (Excel = source de vérité) ───────")
        db_schools = (await s.execute(text(
            "SELECT id, name, code_ecole, director, region, city FROM schools WHERE code_ecole IS NOT NULL"
        ))).fetchall()

        name_fixes = 0
        for sch in db_schools:
            db_id, db_name, db_code, db_director, db_region, db_city = sch
            if db_code not in excel_schools:
                continue
            excel = excel_schools[db_code]
            if db_name != excel["name"]:
                print(f"    → [{db_code}] \"{db_name}\" → \"{excel['name']}\"")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE schools SET name = :name WHERE id = :id"
                    ), {"name": excel["name"], "id": db_id})
                name_fixes += 1
        print(f"  {'✓' if not DRY_RUN else '→'} {name_fixes} noms corrigés\n")

        # ── 2. Corriger le champ director des écoles ──────────────────────
        print("── [2/5] Champ director des écoles ──────────────────────")
        director_fixes = 0
        for sch in db_schools:
            db_id, db_name, db_code, db_director, db_region, db_city = sch
            if db_code not in excel_schools:
                continue
            excel = excel_schools[db_code]
            if excel["director"] and db_director != excel["director"]:
                old = db_director or "(vide)"
                print(f"    → [{db_code}] director: \"{old}\" → \"{excel['director']}\"")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE schools SET director = :dir WHERE id = :id"
                    ), {"dir": excel["director"], "id": db_id})
                director_fixes += 1
        print(f"  {'✓' if not DRY_RUN else '→'} {director_fixes} directors corrigés\n")

        # ── 3. Corriger region/city manquants ─────────────────────────────
        print("── [3/5] Region/city manquants ──────────────────────────")
        geo_fixes = 0
        for sch in db_schools:
            db_id, db_name, db_code, db_director, db_region, db_city = sch
            if db_code not in excel_schools:
                continue
            excel = excel_schools[db_code]
            updates = {}
            if not db_region and excel["region"]:
                updates["region"] = excel["region"]
            if not db_city and excel["city"]:
                updates["city"] = excel["city"]
            if updates:
                print(f"    → [{db_code}] {updates}")
                if not DRY_RUN:
                    parts = []
                    params = {"id": db_id}
                    for k, v in updates.items():
                        parts.append(f"{k} = :{k}")
                        params[k] = v
                    await s.execute(text(
                        f"UPDATE schools SET {', '.join(parts)} WHERE id = :id"
                    ), params)
                geo_fixes += 1
        print(f"  {'✓' if not DRY_RUN else '→'} {geo_fixes} écoles géo corrigées\n")

        # ── 4. Corriger les liens superviseur → école ─────────────────────
        print("── [4/5] Liens superviseur → école ──────────────────────")

        # Build map: director_name → school_id (from Excel code_ecole → DB school)
        if not DRY_RUN:
            await s.flush()
        db_schools_fresh = (await s.execute(text(
            "SELECT id, code_ecole FROM schools WHERE code_ecole IS NOT NULL"
        ))).fetchall()
        code_to_dbid: dict[int, str] = {r[1]: r[0] for r in db_schools_fresh}

        director_to_school: dict[str, str] = {}
        for code, info in excel_schools.items():
            if info["director"] and code in code_to_dbid:
                director_to_school[info["director"]] = code_to_dbid[code]

        sups = (await s.execute(text(
            "SELECT id, name, school_id FROM users WHERE role = 'superviseur'"
        ))).fetchall()

        link_fixes = 0
        for sup in sups:
            sup_id, sup_name, sup_school_id = sup
            if sup_name in director_to_school:
                correct_school = director_to_school[sup_name]
                if str(sup_school_id) != str(correct_school):
                    old_school = (await s.execute(text(
                        "SELECT name FROM schools WHERE id = :id"
                    ), {"id": sup_school_id})).scalar() if sup_school_id else "(aucune)"
                    new_school = (await s.execute(text(
                        "SELECT name FROM schools WHERE id = :id"
                    ), {"id": correct_school})).scalar()
                    print(f"    → {sup_name}: \"{old_school}\" → \"{new_school}\"")
                    if not DRY_RUN:
                        await s.execute(text(
                            "UPDATE users SET school_id = :sid WHERE id = :id"
                        ), {"sid": correct_school, "id": sup_id})
                    link_fixes += 1
        print(f"  {'✓' if not DRY_RUN else '→'} {link_fixes} liens corrigés\n")

        # ── 5. Auto-assigner les enseignants ──────────────────────────────
        print("── [5/5] Auto-assignation enseignants → superviseurs ────")
        if not DRY_RUN:
            await s.flush()

        sups_fresh = (await s.execute(text(
            "SELECT id, name, school_id, classes FROM users WHERE role = 'superviseur'"
        ))).fetchall()

        assign_fixes = 0
        for sup in sups_fresh:
            sup_id, sup_name, sup_school_id, sup_classes = sup
            if not sup_school_id:
                continue

            teacher_ids = (await s.execute(text(
                "SELECT id FROM users WHERE role = 'enseignant' AND school_id = :sid"
            ), {"sid": sup_school_id})).fetchall()

            teacher_id_list = [str(r[0]) for r in teacher_ids]

            if not teacher_id_list:
                continue

            current = set(sup_classes or [])
            target = set(teacher_id_list)

            if current != target:
                added = target - current
                print(f"    → {sup_name}: {len(current)} → {len(target)} enseignants (+{len(added)})")
                if not DRY_RUN:
                    await s.execute(text(
                        "UPDATE users SET classes = :cls WHERE id = :id"
                    ), {"cls": list(target), "id": sup_id})
                assign_fixes += 1

        print(f"  {'✓' if not DRY_RUN else '→'} {assign_fixes} superviseurs mis à jour\n")

        # ── Résumé ────────────────────────────────────────────────────────
        if not DRY_RUN:
            await s.commit()

        # Stats finales
        if not DRY_RUN:
            total_sups = (await s.execute(text("SELECT COUNT(*) FROM users WHERE role = 'superviseur'"))).scalar()
            sups_with_teachers = (await s.execute(text(
                "SELECT COUNT(*) FROM users WHERE role = 'superviseur' AND classes IS NOT NULL AND array_length(classes, 1) > 0"
            ))).scalar()
            dup_names = (await s.execute(text(
                "SELECT COUNT(*) FROM (SELECT name FROM schools GROUP BY name HAVING COUNT(*) > 1) sub"
            ))).scalar()

            print(f"{'='*60}")
            print(f"  ✅ Corrections appliquées !")
            print(f"  Noms d'écoles corrigés : {name_fixes}")
            print(f"  Directors corrigés : {director_fixes}")
            print(f"  Géo corrigées : {geo_fixes}")
            print(f"  Liens superviseur→école : {link_fixes}")
            print(f"  Assignations enseignants : {assign_fixes}")
            print(f"  Doublons noms écoles restants : {dup_names}")
            print(f"  Superviseurs avec enseignants : {sups_with_teachers}/{total_sups}")
            print(f"{'='*60}\n")
        else:
            print(f"{'='*60}")
            print(f"  DRY-RUN — Résumé des actions prévues :")
            print(f"  Noms d'écoles à corriger : {name_fixes}")
            print(f"  Directors à corriger : {director_fixes}")
            print(f"  Géo à corriger : {geo_fixes}")
            print(f"  Liens superviseur→école : {link_fixes}")
            print(f"  Assignations enseignants : {assign_fixes}")
            print(f"\n  → Relancer avec --apply pour exécuter")
            print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run())
