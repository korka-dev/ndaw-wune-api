#!/usr/bin/env python3
"""
Import Excel → Base de données NDAW WUNE
=========================================
Lit le fichier LISTES_ELEVES_*.xlsx et insère dans l'ordre :
  1. Schools      (IEF=region, COMMUNE=city, SCHOOL=name)
  2. SchoolClasse (une par (school, Classe))
  3. Enseignants  (Users role=enseignant, liés à leur école)
  4. Élèves       (liés à leur école, avec classe et genre)

Usage :
    python scripts/import_eleves.py <chemin_vers_fichier.xlsx>

Variables d'env attendues (identiques à start.sh) :
    DATABASE_URL   postgresql+asyncpg://...
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

# ── Rendre "app" importable ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Forcer DATABASE_URL vers localhost AVANT tout import de app.core ─────────
# (le .env contient POSTGRES_HOST=db qui est le nom de service Docker,
#  non résolvable depuis l'hôte — on écrase avec localhost uniquement hors Docker)
_DEFAULT_DB = "postgresql+asyncpg://ared_user:ared_secret@localhost:5432/ared_ndawune"
if not os.path.exists("/.dockerenv") and ("DATABASE_URL" not in os.environ or "://db:" in os.environ.get("DATABASE_URL", "")):
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", _DEFAULT_DB).replace(
        "@db:", "@localhost:"
    )
    # Si la var était absente, mettre le défaut localhost
    if os.environ["DATABASE_URL"].startswith("postgresql+asyncpg://$"):
        os.environ["DATABASE_URL"] = _DEFAULT_DB

from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.eleve import Eleve
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.user import User, UserRole, UserStatus

# Mot de passe temporaire pour tous les enseignants créés via import
TEACHER_DEFAULT_PASSWORD = "NdawWune2025!"

# ── Helpers ──────────────────────────────────────────────────────────────────

def split_nom_prenom(full_name: str) -> tuple[str, str | None]:
    """
    Sépare un nom complet en (nom, prenom).
    Convention : dernier mot = nom de famille, le reste = prénom(s).
    Exemple : 'NDEYE MAI DIENG' → nom='DIENG', prenom='NDEYE MAI'
    """
    parts = str(full_name).strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[-1], " ".join(parts[:-1])


def genre_clean(val: str) -> str | None:
    v = str(val).strip().lower()
    if v in ("fille", "f"):
        return "Fille"
    if v in ("garcon", "garçon", "g", "m", "masculin"):
        return "Garcon"
    return None


# ── Étape 1 — Schools ────────────────────────────────────────────────────────

async def import_schools(session, df: pd.DataFrame) -> dict[str, uuid.UUID]:
    """Insère les écoles et retourne un mapping name → id."""
    ecoles = (
        df[["IEF", "COMMUNE", "SCHOOL"]]
        .drop_duplicates(subset=["SCHOOL"])
        .sort_values("SCHOOL")
    )
    school_map: dict[str, uuid.UUID] = {}

    # Charger les écoles existantes
    existing = await session.execute(select(School))
    for s in existing.scalars():
        school_map[s.name] = s.id

    created = 0
    for _, row in ecoles.iterrows():
        name = str(row["SCHOOL"]).strip()
        if name in school_map:
            continue
        school = School(
            id=uuid.uuid4(),
            name=name,
            region=str(row["IEF"]).strip() if pd.notna(row["IEF"]) else None,
            city=str(row["COMMUNE"]).strip() if pd.notna(row["COMMUNE"]) else None,
        )
        session.add(school)
        school_map[name] = school.id
        created += 1

    await session.flush()
    print(f"  ✓ Écoles : {created} créées, {len(school_map) - created} déjà existantes → {len(school_map)} total")
    return school_map


# ── Étape 2 — SchoolClasse ───────────────────────────────────────────────────

async def import_classes(
    session, df: pd.DataFrame, school_map: dict[str, uuid.UUID]
) -> dict[tuple[str, str], uuid.UUID]:
    """Insère les classes et retourne un mapping (school_name, classe_name) → id."""
    classes_df = (
        df[["SCHOOL", "Classe", "NIVEAU"]]
        .drop_duplicates(subset=["SCHOOL", "Classe"])
        .sort_values(["SCHOOL", "Classe"])
    )
    classe_map: dict[tuple[str, str], uuid.UUID] = {}

    # Charger les classes existantes
    existing = await session.execute(select(SchoolClasse))
    for c in existing.scalars():
        # On a besoin du nom de l'école — on retrouve via school_map inversé
        key = (c.school_id, c.name)
        classe_map[key] = c.id  # temporaire, on réindexe après

    # Réindexer par (school_name, classe_name) pour faciliter la suite
    sid_to_name = {v: k for k, v in school_map.items()}
    existing_by_name: dict[tuple[str, str], uuid.UUID] = {}
    existing_res = await session.execute(select(SchoolClasse))
    for c in existing_res.scalars():
        sname = sid_to_name.get(c.school_id, "")
        existing_by_name[(sname, c.name)] = c.id

    created = 0
    for _, row in classes_df.iterrows():
        school_name = str(row["SCHOOL"]).strip()
        classe_name = str(row["Classe"]).strip()
        niveau_name = str(row["NIVEAU"]).strip()
        key = (school_name, classe_name)

        if key in existing_by_name:
            classe_map[key] = existing_by_name[key]
            continue

        school_id = school_map.get(school_name)
        if not school_id:
            print(f"  ⚠ Classe ignorée : école '{school_name}' introuvable")
            continue

        # Calculer l'effectif de cette classe
        effectif = len(df[(df["SCHOOL"] == school_name) & (df["Classe"] == classe_name)])

        sc = SchoolClasse(
            id=uuid.uuid4(),
            name=classe_name,
            niveau=niveau_name,
            effectif=effectif,
            school_id=school_id,
        )
        session.add(sc)
        classe_map[key] = sc.id
        created += 1

    await session.flush()
    print(f"  ✓ Classes : {created} créées, {len(existing_by_name)} déjà existantes → {len(classe_map)} total")
    return classe_map


# ── Étape 3 — Enseignants ────────────────────────────────────────────────────

async def import_enseignants(
    session, df: pd.DataFrame, school_map: dict[str, uuid.UUID]
) -> dict[tuple[str, str], uuid.UUID]:
    """
    Crée un User (role=enseignant) par (SCHOOL, enseignant).
    Agrège les classes et niveaux enseignés.
    Retourne mapping (school_name, teacher_name) → user_id.
    """
    # Grouper par (école, enseignant) pour collecter classes + niveaux
    teacher_info = (
        df.groupby(["SCHOOL", "enseignant"])
        .agg(
            classes=("Classe", lambda x: sorted(x.unique().tolist())),
            niveaux=("NIVEAU", lambda x: sorted(x.unique().tolist())),
        )
        .reset_index()
    )

    teacher_map: dict[tuple[str, str], uuid.UUID] = {}

    # Charger les enseignants existants (par nom + school_id)
    existing_res = await session.execute(
        select(User).where(User.role == UserRole.enseignant)
    )
    existing_teachers = existing_res.scalars().all()
    sid_to_name = {v: k for k, v in school_map.items()}
    for t in existing_teachers:
        sname = sid_to_name.get(t.school_id, "")
        teacher_map[(sname, t.name)] = t.id

    pwd_hash = hash_password(TEACHER_DEFAULT_PASSWORD)
    created = 0

    for _, row in teacher_info.iterrows():
        school_name  = str(row["SCHOOL"]).strip()
        teacher_name = str(row["enseignant"]).strip()
        key = (school_name, teacher_name)

        if key in teacher_map:
            continue

        school_id = school_map.get(school_name)
        if not school_id:
            continue

        user = User(
            id=uuid.uuid4(),
            name=teacher_name,
            email=None,        # pas d'email dans l'Excel
            phone=None,        # pas de téléphone dans l'Excel
            password_hash=pwd_hash,
            role=UserRole.enseignant,
            status=UserStatus.actif,
            must_change_password=True,
            school_id=school_id,
            classes=row["classes"],
            niveau=row["niveaux"],
        )
        session.add(user)
        teacher_map[key] = user.id
        created += 1

    await session.flush()
    print(f"  ✓ Enseignants : {created} créés, {len(teacher_map) - created} déjà existants → {len(teacher_map)} total")
    return teacher_map


# ── Étape 4 — Élèves ─────────────────────────────────────────────────────────

async def import_eleves(
    session, df: pd.DataFrame, school_map: dict[str, uuid.UUID]
) -> None:
    """Insère les élèves en évitant les doublons (contrainte uq_eleve_school_classe_nom)."""
    # Charger les élèves existants comme set de clés (school_id, classe, nom, prenom)
    existing_res = await session.execute(select(Eleve))
    existing_set: set[tuple] = {
        (str(e.school_id), e.classe, e.nom, e.prenom or "")
        for e in existing_res.scalars()
    }

    created = 0
    skipped = 0
    errors  = 0

    for _, row in df.iterrows():
        school_name = str(row["SCHOOL"]).strip()
        classe      = str(row["Classe"]).strip()
        full_name   = str(row["name"]).strip() if pd.notna(row["name"]) else ""
        genre_raw   = str(row["Sexe"]).strip() if pd.notna(row["Sexe"]) else ""

        if not full_name:
            errors += 1
            continue

        nom, prenom = split_nom_prenom(full_name)
        school_id   = school_map.get(school_name)
        if not school_id:
            errors += 1
            continue

        key = (str(school_id), classe, nom, prenom or "")
        if key in existing_set:
            skipped += 1
            continue

        eleve = Eleve(
            id=uuid.uuid4(),
            nom=nom,
            prenom=prenom,
            classe=classe,
            genre=genre_clean(genre_raw),
            statut="actif",
            school_id=school_id,
            session_id=None,
        )
        session.add(eleve)
        existing_set.add(key)
        created += 1

        # Flush par lots de 500 pour éviter une transaction trop grosse
        if created % 500 == 0:
            await session.flush()
            print(f"    … {created} élèves insérés")

    await session.flush()
    print(f"  ✓ Élèves : {created} créés, {skipped} doublons ignorés, {errors} erreurs → {created + skipped} traités")


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(xlsx_path: str) -> None:
    print(f"\n📂 Lecture de {xlsx_path} …")
    df = pd.read_excel(xlsx_path, header=0)
    df.columns = [str(c).strip() for c in df.columns]

    # Le fichier peut utiliser "tuteur" au lieu de "enseignant" pour la colonne enseignant
    if "enseignant" not in df.columns and "tuteur" in df.columns:
        df = df.rename(columns={"tuteur": "enseignant"})

    # Nettoyer les valeurs texte
    for col in ["IEF", "COMMUNE", "SCHOOL", "SCHOOL_CLASSE", "enseignant", "NIVEAU", "Classe", "name", "Sexe"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace("nan", None)

    df = df.dropna(subset=["SCHOOL", "name"])
    print(f"  {len(df)} lignes valides sur {len(df) + df['name'].isna().sum()} total")

    print("\n[1/4] Écoles …")
    print("[2/4] Classes …")
    print("[3/4] Enseignants …")
    print("[4/4] Élèves …\n")

    async with AsyncSessionLocal() as session:
        try:
            print("── [1/4] Écoles ─────────────────────────────────────────")
            school_map = await import_schools(session, df)

            print("── [2/4] Classes ────────────────────────────────────────")
            await import_classes(session, df, school_map)

            print("── [3/4] Enseignants ────────────────────────────────────")
            await import_enseignants(session, df, school_map)

            print("── [4/4] Élèves ─────────────────────────────────────────")
            await import_eleves(session, df, school_map)

            await session.commit()
            print("\n✅  Import terminé avec succès !")
            print(f"   Mot de passe temporaire des enseignants : {TEACHER_DEFAULT_PASSWORD}")
            print("   (les enseignants devront le changer à la première connexion)")

        except Exception as e:
            await session.rollback()
            print(f"\n❌  Erreur — rollback effectué : {e}")
            raise


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python scripts/import_eleves.py <fichier.xlsx>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
