#!/usr/bin/env python3
"""
Import Excel « ListeDesELevesPricipauxRemplacement.xlsx » → Base NDAW WUNE
==========================================================================
Colonnes attendues :
    code_ief | ief | code_commune | commune | Directeur | code_ecole |
    ecole | classe_num | code_eleve | nom_eleve | code_Classe | selection

Le script insère dans l'ordre :
  1. Schools     — crée les écoles absentes (code_ecole, ecole, ief→region, commune→city)
  2. Superviseurs — le champ « Directeur » correspond à un superviseur (role=superviseur)
  3. SchoolClasses — une par (school, code_Classe)
  4. Élèves      — avec code_eleve, classe, statut (Titulaire/Remplacant)

Idempotent : on vérifie les doublons avant chaque insertion.

Usage (local) :
    python scripts/import_remplacement.py <chemin.xlsx>

Usage (production VPS) :
    python scripts/import_remplacement.py <chemin.xlsx>
    (s'assurer que DATABASE_URL pointe vers la bonne base)
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import openpyxl
from sqlalchemy import select

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

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.eleve import Eleve
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.user import User, UserRole, UserStatus

SUPERVISEUR_DEFAULT_PASSWORD = "NdawWune2025!"

HEADER = [
    "code_ief", "ief", "code_commune", "commune", "directeur",
    "code_ecole", "ecole", "classe_num", "code_eleve", "nom_eleve",
    "code_classe", "selection",
]


def read_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if len(row) < 12:
            continue
        d = {}
        for j, key in enumerate(HEADER):
            val = row[j]
            d[key] = str(val).strip() if val is not None else None
        if d["ecole"] and d["nom_eleve"]:
            rows.append(d)
    wb.close()
    return rows


def split_nom_prenom(full_name: str) -> tuple[str, str | None]:
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], " ".join(parts[:-1])


def extract_niveau(code_classe: str) -> str:
    c = code_classe.strip().upper()
    if c.startswith("CE1"):
        return "CE1"
    if c.startswith("CP"):
        return "CP"
    if c.startswith("CE2"):
        return "CE2"
    if c.startswith("CM"):
        return "CM1" if "1" in c else "CM2"
    return c.split()[0] if c else c


# ── Étape 1 — Schools ──────────────────────────────────────────────────────────

async def import_schools(session, rows: list[dict]) -> dict[str, uuid.UUID]:
    seen_ecoles: dict[str, dict] = {}
    for r in rows:
        code = r["code_ecole"]
        if code and code not in seen_ecoles:
            seen_ecoles[code] = {
                "code_ecole": int(code),
                "name": r["ecole"],
                "region": r["ief"],
                "city": r["commune"],
                "director": r["directeur"],
            }

    existing = await session.execute(select(School))
    school_by_code: dict[int, uuid.UUID] = {}
    school_by_name: dict[str, uuid.UUID] = {}
    for s in existing.scalars():
        if s.code_ecole is not None:
            school_by_code[s.code_ecole] = s.id
        school_by_name[s.name] = s.id

    code_to_uuid: dict[str, uuid.UUID] = {}
    created = 0
    updated = 0

    for code_str, info in seen_ecoles.items():
        code_int = info["code_ecole"]

        if code_int in school_by_code:
            code_to_uuid[code_str] = school_by_code[code_int]
            continue

        if info["name"] in school_by_name:
            sid = school_by_name[info["name"]]
            code_to_uuid[code_str] = sid
            result = await session.execute(select(School).where(School.id == sid))
            school = result.scalar_one()
            if school.code_ecole is None:
                school.code_ecole = code_int
                updated += 1
            if school.director is None and info["director"]:
                school.director = info["director"]
            continue

        school = School(
            id=uuid.uuid4(),
            name=info["name"],
            code_ecole=code_int,
            region=info["region"],
            city=info["city"],
            director=info["director"],
        )
        session.add(school)
        code_to_uuid[code_str] = school.id
        school_by_code[code_int] = school.id
        school_by_name[info["name"]] = school.id
        created += 1

    await session.flush()
    existing_count = len(code_to_uuid) - created
    print(f"  ✓ Écoles : {created} créées, {updated} mises à jour, {existing_count - updated} déjà OK → {len(code_to_uuid)} total")
    return code_to_uuid


# ── Étape 2 — Superviseurs ─────────────────────────────────────────────────────

async def import_superviseurs(
    session, rows: list[dict], code_to_school: dict[str, uuid.UUID]
) -> dict[str, uuid.UUID]:
    seen: dict[str, dict] = {}
    for r in rows:
        code = r["code_ecole"]
        directeur = r["directeur"]
        if directeur and code and code in code_to_school and directeur not in seen:
            seen[directeur] = {"name": directeur, "school_id": code_to_school[code]}

    existing = await session.execute(
        select(User).where(User.role == UserRole.superviseur)
    )
    existing_by_name_school: dict[tuple[str, str], uuid.UUID] = {}
    for u in existing.scalars():
        existing_by_name_school[(u.name, str(u.school_id))] = u.id

    pwd_hash = hash_password(SUPERVISEUR_DEFAULT_PASSWORD)
    sup_map: dict[str, uuid.UUID] = {}
    created = 0

    for name, info in seen.items():
        key = (name, str(info["school_id"]))
        if key in existing_by_name_school:
            sup_map[name] = existing_by_name_school[key]
            continue

        user = User(
            id=uuid.uuid4(),
            name=name,
            email=None,
            phone=None,
            password_hash=pwd_hash,
            role=UserRole.superviseur,
            status=UserStatus.actif,
            must_change_password=True,
            school_id=info["school_id"],
        )
        session.add(user)
        sup_map[name] = user.id
        created += 1

    await session.flush()
    print(f"  ✓ Superviseurs : {created} créés, {len(sup_map) - created} déjà existants → {len(sup_map)} total")
    return sup_map


# ── Étape 3 — Classes ──────────────────────────────────────────────────────────

async def import_classes(
    session, rows: list[dict], code_to_school: dict[str, uuid.UUID]
) -> dict[tuple[str, str], uuid.UUID]:
    seen_classes: dict[tuple[str, str], str] = {}
    class_effectifs: dict[tuple[str, str], int] = {}
    for r in rows:
        code_ecole = r["code_ecole"]
        code_classe = r["code_classe"]
        if code_ecole and code_classe and code_ecole in code_to_school:
            key = (code_ecole, code_classe)
            if key not in seen_classes:
                seen_classes[key] = extract_niveau(code_classe)
                class_effectifs[key] = 0
            class_effectifs[key] += 1

    existing = await session.execute(select(SchoolClasse))
    existing_by_key: dict[tuple[str, str], uuid.UUID] = {}
    for c in existing.scalars():
        existing_by_key[(str(c.school_id), c.name)] = c.id

    classe_map: dict[tuple[str, str], uuid.UUID] = {}
    created = 0

    for (code_ecole, classe_name), niveau in seen_classes.items():
        school_id = code_to_school[code_ecole]
        db_key = (str(school_id), classe_name)

        if db_key in existing_by_key:
            classe_map[(code_ecole, classe_name)] = existing_by_key[db_key]
            continue

        sc = SchoolClasse(
            id=uuid.uuid4(),
            name=classe_name,
            niveau=niveau,
            effectif=class_effectifs.get((code_ecole, classe_name)),
            school_id=school_id,
        )
        session.add(sc)
        classe_map[(code_ecole, classe_name)] = sc.id
        created += 1

    await session.flush()
    print(f"  ✓ Classes : {created} créées, {len(classe_map) - created} déjà existantes → {len(classe_map)} total")
    return classe_map


# ── Étape 4 — Élèves ───────────────────────────────────────────────────────────

async def import_eleves(
    session, rows: list[dict], code_to_school: dict[str, uuid.UUID]
) -> None:
    existing = await session.execute(select(Eleve))
    existing_codes: set[str] = set()
    existing_keys: set[tuple] = set()
    for e in existing.scalars():
        if e.code_eleve:
            existing_codes.add(e.code_eleve)
        existing_keys.add((str(e.school_id), e.classe, e.nom, e.prenom or ""))

    created = 0
    skipped = 0
    errors = 0

    for r in rows:
        code_ecole = r["code_ecole"]
        code_eleve = r["code_eleve"]
        nom_eleve = r["nom_eleve"]
        code_classe = r["code_classe"]
        selection = r["selection"]

        if not nom_eleve or not code_ecole or code_ecole not in code_to_school:
            errors += 1
            continue

        if code_eleve and code_eleve in existing_codes:
            skipped += 1
            continue

        school_id = code_to_school[code_ecole]
        nom, prenom = split_nom_prenom(nom_eleve)

        natural_key = (str(school_id), code_classe or "", nom, prenom or "")
        if natural_key in existing_keys:
            skipped += 1
            continue

        statut = "actif"
        if selection and selection.lower() == "remplacant":
            statut = "remplacant"

        eleve = Eleve(
            id=uuid.uuid4(),
            nom=nom,
            prenom=prenom,
            code_eleve=code_eleve,
            classe=code_classe or "",
            genre=None,
            date_naissance=None,
            statut=statut,
            school_id=school_id,
            session_id=None,
        )
        session.add(eleve)
        if code_eleve:
            existing_codes.add(code_eleve)
        existing_keys.add(natural_key)
        created += 1

        if created % 500 == 0:
            await session.flush()
            print(f"    … {created} élèves insérés")

    await session.flush()
    print(f"  ✓ Élèves : {created} créés, {skipped} doublons ignorés, {errors} erreurs → {created + skipped} traités")


# ── Main ────────────────────────────────────────────────────────────────────────

async def run(xlsx_path: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Import Élèves/Écoles/Superviseurs — NDAW WUNE")
    print(f"{'='*60}")
    print(f"\n📂 Lecture de {xlsx_path} …")

    rows = read_xlsx(xlsx_path)
    print(f"  {len(rows)} lignes valides")

    ecoles_u = len({r['code_ecole'] for r in rows if r['code_ecole']})
    sups_u = len({r['directeur'] for r in rows if r['directeur']})
    classes_u = len({(r['code_ecole'], r['code_classe']) for r in rows if r['code_ecole'] and r['code_classe']})
    eleves_u = len({r['code_eleve'] for r in rows if r['code_eleve']})
    print(f"  → {ecoles_u} écoles, {sups_u} superviseurs, {classes_u} classes, {eleves_u} élèves uniques")

    sels = {}
    for r in rows:
        s = r.get("selection", "?")
        sels[s] = sels.get(s, 0) + 1
    print(f"  → Répartition sélection : {sels}")

    print(f"\n  Base de données : {os.environ.get('DATABASE_URL', '???')[:50]}…")

    async with AsyncSessionLocal() as session:
        try:
            print("\n── [1/4] Écoles ─────────────────────────────────────────")
            code_to_school = await import_schools(session, rows)

            print("\n── [2/4] Superviseurs (Directeurs) ──────────────────────")
            await import_superviseurs(session, rows, code_to_school)

            print("\n── [3/4] Classes ────────────────────────────────────────")
            await import_classes(session, rows, code_to_school)

            print("\n── [4/4] Élèves ─────────────────────────────────────────")
            await import_eleves(session, rows, code_to_school)

            await session.commit()
            print(f"\n{'='*60}")
            print(f"  ✅  Import terminé avec succès !")
            print(f"  Mot de passe superviseurs : {SUPERVISEUR_DEFAULT_PASSWORD}")
            print(f"  (changement obligatoire à la première connexion)")
            print(f"{'='*60}\n")

        except Exception as e:
            await session.rollback()
            print(f"\n❌  Erreur — rollback effectué : {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python scripts/import_remplacement.py <fichier.xlsx>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
