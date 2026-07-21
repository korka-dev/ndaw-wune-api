#!/usr/bin/env python3
"""
Import des acteurs NWV 2026 depuis le fichier Excel.

Actions :
  1. Supprime TOUS les enseignants et superviseurs existants
  2. Importe les superviseurs et tuteurs du fichier Excel
  3. Lie chaque utilisateur à son école (code_ecole) et à la session active
  4. Utilise le numéro WhatsApp comme numéro de téléphone (identifiant de connexion)

Usage :
    python scripts/import_acteurs_nwv2026.py <chemin_vers_xlsx>
"""

import hashlib
import subprocess
import sys
import uuid
from pathlib import Path

import bcrypt
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Config ────────────────────────────────────────────────────────────────────
SESSION_ID  = "5b759235-a384-4e4a-9de1-e2c015e2ca85"
DEFAULT_PWD = "Passer123"
DB_USER     = "ared_user"
DB_NAME     = "ared_ndawune"
DOCKER_SVC  = "db"   # nom du service dans docker-compose


def _hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _clean_phone(val) -> str | None:
    if val is None or (isinstance(val, float) and str(val) == "nan"):
        return None
    s = str(val).strip().replace(" ", "").replace("-", "")
    for prefix in ("+221", "00221", "221"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    digits = "".join(c for c in s if c.isdigit())
    # Supprimer le .0 résiduel Excel (ex: "779687423.0" → "779687423")
    if "." in s:
        digits = s.split(".")[0]
        digits = "".join(c for c in digits if c.isdigit())
    if len(digits) >= 9:
        return digits[-9:]
    return digits if digits else None


def _clean_name(val) -> str | None:
    if val is None or (isinstance(val, float) and str(val) == "nan"):
        return None
    s = str(val).strip()
    return s if s else None


def _esc(s: str) -> str:
    """Échappe les apostrophes pour SQL."""
    return s.replace("'", "''")


def run_sql(sql: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", DOCKER_SVC,
         "psql", "-U", DB_USER, "-d", DB_NAME],
        input=sql.encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
    )
    output = result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
    return output


def main(xlsx_path: str) -> None:
    print(f"📖  Lecture du fichier : {xlsx_path}")
    df = pd.read_excel(xlsx_path, header=0)
    df.columns = [
        "ief", "code_ief", "commune", "code_commune", "ecole", "code_ecole",
        "confirme", "superviseur", "whatsapp_sup", "wave_sup",
        "tuteur1", "whatsapp_t1", "wave_t1",
        "tuteur2", "whatsapp_t2", "wave_t2",
    ]
    df["code_ecole"] = pd.to_numeric(df["code_ecole"], errors="coerce")

    # ── Hash du mot de passe (une seule fois pour tous les utilisateurs) ───────
    print(f"🔐  Hash du mot de passe par défaut…")
    pwd_hash = _hash_password(DEFAULT_PWD)

    # ── Récupérer le mapping code_ecole → school UUID ─────────────────────────
    school_rows = run_sql("SELECT id, code_ecole FROM schools WHERE code_ecole IS NOT NULL;")
    school_map: dict[int, str] = {}
    for line in school_rows.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 2 and "-" in parts[0] and parts[1].isdigit():
            school_map[int(parts[1])] = parts[0]
    print(f"   → {len(school_map)} école(s) trouvée(s) en base")

    # ── Récupérer les classes par école (ordre alphabétique) ──────────────────
    classes_rows = run_sql("""
SELECT s.id, sc.name
FROM schools s
JOIN school_classes sc ON sc.school_id = s.id
WHERE s.code_ecole IS NOT NULL
ORDER BY s.id, sc.name;
""")
    school_classes_map: dict[str, list[str]] = {}
    for line in classes_rows.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 2 and len(parts[0]) == 36 and parts[0].count("-") == 4:
            s_uuid, cls_name = parts[0], parts[1]
            school_classes_map.setdefault(s_uuid, []).append(cls_name)
    print(f"   → {len(school_classes_map)} école(s) avec classes chargées")

    # ── Construire les INSERT ──────────────────────────────────────────────────
    phones_seen: set[str] = set()
    insert_users: list[str] = []
    insert_sessions: list[str] = []
    warnings: list[str] = []

    created_sup = 0
    created_tut = 0
    skipped     = 0

    def _classes_sql(classes: list[str] | None) -> str:
        if not classes:
            return "NULL"
        items = ", ".join(f"'{_esc(c)}'" for c in classes)
        return f"ARRAY[{items}]"

    def add_user(name: str, phone: str | None, role: str,
                 school_uuid: str | None, classes: list[str] | None = None) -> str | None:
        """Retourne l'UUID créé, ou None si ignoré (doublon/nom vide)."""
        nonlocal created_sup, created_tut, skipped

        if not name:
            return None

        if phone and phone in phones_seen:
            warnings.append(f"[DOUBLON] {role} « {name} » — tél {phone} déjà utilisé, ignoré")
            skipped += 1
            return None

        if phone:
            phones_seen.add(phone)

        uid         = str(uuid.uuid4())
        phone_sql   = f"'{phone}'"      if phone        else "NULL"
        school_sql  = f"'{school_uuid}'" if school_uuid else "NULL"
        classes_sql = _classes_sql(classes)

        insert_users.append(
            f"('{uid}', '{_esc(name)}', {phone_sql}, NULL, "
            f"'{_esc(pwd_hash)}', '{role}', 'actif', TRUE, 'full', "
            f"{school_sql}, {classes_sql}, NOW(), NOW())"
        )
        insert_sessions.append(
            f"('{uid}', '{SESSION_ID}', NOW(), NOW())"
        )

        if role == "superviseur":
            created_sup += 1
        else:
            created_tut += 1

        return uid

    for _, row in df.iterrows():
        code_ecole = None if pd.isna(row["code_ecole"]) else int(row["code_ecole"])
        school_uuid = school_map.get(code_ecole) if code_ecole else None
        s_classes   = school_classes_map.get(school_uuid, []) if school_uuid else []

        if school_uuid is None and code_ecole:
            warnings.append(
                f"[ÉCOLE INCONNUE] Code {code_ecole} "
                f"({_clean_name(row['ecole']) or '?'}) — sans école"
            )

        # Tuteur 1 → 1ère classe de l'école (ordre alphabétique)
        # (créé avant le superviseur pour récupérer son UUID)
        t1_name  = _clean_name(row["tuteur1"])
        t1_phone = _clean_phone(row["whatsapp_t1"])
        t1_class = [s_classes[0]] if s_classes else None
        t1_uid   = add_user(t1_name, t1_phone, "enseignant", school_uuid, classes=t1_class) if t1_name else None

        # Tuteur 2 → 2ème classe de l'école (optionnel)
        t2_name  = _clean_name(row["tuteur2"])
        t2_phone = _clean_phone(row["whatsapp_t2"])
        t2_class = [s_classes[1]] if len(s_classes) > 1 else None
        t2_uid   = add_user(t2_name, t2_phone, "enseignant", school_uuid, classes=t2_class) if t2_name else None

        # Superviseur → rattaché aux tuteurs de son école
        sup_teacher_uids = [uid for uid in [t1_uid, t2_uid] if uid is not None]
        sup_name  = _clean_name(row["superviseur"])
        sup_phone = _clean_phone(row["whatsapp_sup"])
        if sup_name:
            add_user(sup_name, sup_phone, "superviseur", school_uuid,
                     classes=sup_teacher_uids if sup_teacher_uids else None)

    # ── Construire le SQL final ───────────────────────────────────────────────
    users_values   = ",\n  ".join(insert_users)
    session_values = ",\n  ".join(insert_sessions)

    sql = f"""
BEGIN;

-- 1. Supprimer enseignants et superviseurs existants
DELETE FROM users WHERE role IN ('enseignant', 'superviseur');

-- 2. Insérer les nouveaux utilisateurs
INSERT INTO users
  (id, name, phone, email, password_hash, role, status,
   must_change_password, app_access, school_id, classes, created_at, updated_at)
VALUES
  {users_values};

-- 3. Rattacher à la session active
INSERT INTO teacher_sessions (teacher_id, session_id, created_at, updated_at)
VALUES
  {session_values};

COMMIT;

-- Vérification
SELECT role, COUNT(*) FROM users WHERE role IN ('enseignant','superviseur') GROUP BY role;
"""

    # ── Exécution ─────────────────────────────────────────────────────────────
    print(f"\n🗑  Suppression + import de {len(insert_users)} utilisateur(s)…")
    output = run_sql(sql)
    print(output)

    # ── Résumé ────────────────────────────────────────────────────────────────
    print("═" * 55)
    print("✅  Import terminé")
    print(f"   Superviseurs créés : {created_sup}")
    print(f"   Tuteurs créés      : {created_tut}")
    print(f"   Doublons ignorés   : {skipped}")
    print(f"   Mot de passe       : {DEFAULT_PWD}")
    print(f"   Connexion          : numéro WhatsApp (ex: 779687423)")
    if warnings:
        print(f"\n⚠  {len(warnings)} avertissement(s) :")
        for w in warnings:
            print(f"   {w}")
    print("═" * 55)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python scripts/import_acteurs_nwv2026.py <chemin.xlsx>")
        sys.exit(1)
    main(sys.argv[1])
