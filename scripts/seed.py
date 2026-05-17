#!/usr/bin/env python3
"""
Seed initial : crée un compte administrateur par défaut.

Usage :
    python scripts/seed.py

Variables d'environnement attendues (dans .env) :
    SEED_ADMIN_EMAIL    — e-mail du premier admin   (défaut : admin@ared.sn)
    SEED_ADMIN_NAME     — nom affiché               (défaut : Administrateur ARED)
    SEED_ADMIN_PASSWORD — mot de passe initial      (défaut : P@sser123)

Le compte est créé avec must_change_password=True : l'admin devra changer
son mot de passe à la première connexion.

Si un utilisateur avec le même e-mail existe déjà, le seed est ignoré.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

# ── Rendre "app" importable depuis la racine du projet ───────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus


# ── Paramètres du compte admin ────────────────────────────────────────────────
ADMIN_EMAIL           = os.getenv("SEED_ADMIN_EMAIL",    "admin@ared.sn")
ADMIN_NAME            = os.getenv("SEED_ADMIN_NAME",     "Administrateur ARED")
ADMIN_PASSWORD        = os.getenv("SEED_ADMIN_PASSWORD", settings.DEFAULT_USER_PASSWORD)
ADMIN_MUST_CHANGE_PWD = os.getenv("SEED_MUST_CHANGE_PASSWORD", "false").lower() == "true"


async def seed(session: AsyncSession) -> None:
    # Vérifie si le compte existe déjà
    existing = await session.scalar(
        select(User).where(User.email == ADMIN_EMAIL)
    )
    if existing:
        print(f"[seed] Compte admin '{ADMIN_EMAIL}' déjà présent — ignoré.")
        return

    admin = User(
        id                   = uuid.uuid4(),
        name                 = ADMIN_NAME,
        email                = ADMIN_EMAIL,
        password_hash        = hash_password(ADMIN_PASSWORD),
        role                 = UserRole.admin,
        status               = UserStatus.actif,
        must_change_password = ADMIN_MUST_CHANGE_PWD,
    )
    session.add(admin)
    await session.commit()
    print(f"[seed] ✅  Compte admin créé : {ADMIN_EMAIL}  (mot de passe : {ADMIN_PASSWORD})")
    print("[seed]     → L'admin devra changer ce mot de passe à la première connexion.")


async def main() -> None:
    print("[seed] Connexion à la base de données…")
    async with async_session_factory() as session:
        await seed(session)
    print("[seed] Terminé.")


if __name__ == "__main__":
    asyncio.run(main())
