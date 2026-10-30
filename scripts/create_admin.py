#!/usr/bin/env python3
"""
Crée le compte administrateur initial s'il n'existe pas encore.

Lancé à chaque démarrage du conteneur (voir Dockerfile) — il doit donc être
idempotent : un compte existant n'est jamais modifié, pour ne pas écraser un
mot de passe changé depuis le dashboard.

Variables lues dans l'environnement (.env) :
    ADMIN_EMAIL     (défaut : adiallo@gmail.com)
    ADMIN_NAME      (défaut : Admin Diallo)
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

from sqlalchemy import select

from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus


async def create_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "adiallo@gmail.com").strip()
    name = os.getenv("ADMIN_NAME", "Admin Diallo").strip()
    password = os.getenv("ADMIN_PASSWORD", "")

    if not password:
        print("[*] ADMIN_PASSWORD vide — création du compte admin ignorée.")
        return

    async with async_session_factory() as session:
        existing = await session.scalar(select(User).where(User.email == email))

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
                must_change_password=False,
            )
        )
        await session.commit()
        print(f"[+] ✅ Compte admin '{email}' créé (mot de passe : ADMIN_PASSWORD du .env).")


def main() -> None:
    try:
        asyncio.run(create_admin())
    except Exception as e:
        print(f"[❌] Une erreur est survenue lors de la création du compte : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
