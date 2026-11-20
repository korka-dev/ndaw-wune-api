#!/usr/bin/env python3
"""
Crée le compte administrateur initial s'il n'existe pas encore.

Commande PONCTUELLE (rapport V2, APP-03) : elle n'est plus lancée au démarrage
du conteneur. Elle reste idempotente : un compte existant n'est jamais modifié,
pour ne pas écraser un mot de passe changé depuis le dashboard. Le compte créé
doit changer son mot de passe à la première connexion. Aucun secret n'est affiché.

Variables lues dans l'environnement (.env) :
    ADMIN_EMAIL     (obligatoire)
    ADMIN_NAME      (défaut : Administrateur)
    ADMIN_PASSWORD  (vide → aucun compte n'est créé)

Usage :
    python scripts/create_admin.py
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Rendre "app" importable depuis la racine du projet backend
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus


async def create_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    name = os.getenv("ADMIN_NAME", "").strip() or "Administrateur"
    password = os.getenv("ADMIN_PASSWORD", "")

    if not password:
        print("[*] ADMIN_PASSWORD vide — création du compte admin ignorée.")
        return
    if not email:
        print("[!] ADMIN_EMAIL vide — renseignez-le dans .env.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("[!] ADMIN_PASSWORD doit contenir au moins 8 caractères.", file=sys.stderr)
        sys.exit(1)

    async with async_session_factory() as session:
        existing = await session.scalar(select(User).where(func.lower(User.email) == email))

        if existing:
            print(f"[*] Compte admin '{email}' déjà présent — inchangé.")
            return

        session.add(
            User(
                id=uuid.uuid4(),
                name=name,
                email=email,
                password_hash=hash_password(password),
                role=UserRole.admin,
                status=UserStatus.actif,
                must_change_password=True,   # secret initial = temporaire
            )
        )
        await session.commit()
        print(f"[+] ✅ Compte admin '{email}' créé — mot de passe temporaire à changer à la 1re connexion.")


def main() -> None:
    try:
        asyncio.run(create_admin())
    except Exception as e:
        print(f"[❌] Une erreur est survenue lors de la création du compte : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
