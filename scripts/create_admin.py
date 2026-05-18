#!/usr/bin/env python3
"""
Script pour créer un compte administrateur spécifique.

Usage :
    python scripts/create_admin.py
"""

import asyncio
import sys
import uuid
from pathlib import Path

# Rendre "app" importable depuis la racine du projet backend
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole, UserStatus


async def create_admin() -> None:
    email = "adiallo@gmail.com"
    password = "P@sser123"
    name = "Admin Diallo"

    print(f"[*] Connexion à la base de données...")
    async with async_session_factory() as session:
        # Vérifier si l'utilisateur existe déjà
        existing = await session.scalar(
            select(User).where(User.email == email)
        )
        
        if existing:
            print(f"[!] L'utilisateur avec l'e-mail '{email}' existe déjà.")
            
            # Optionnel : Mettre à jour son rôle et son mot de passe s'il existe déjà
            print(f"[*] Mise à jour du mot de passe et rôle admin pour '{email}'...")
            existing.role = UserRole.admin
            existing.status = UserStatus.actif
            existing.password_hash = hash_password(password)
            existing.must_change_password = False
            await session.commit()
            print(f"[+] ✅ Compte admin '{email}' mis à jour avec succès avec le mot de passe : {password}")
            return

        # Création du compte administrateur
        new_admin = User(
            id=uuid.uuid4(),
            name=name,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.admin,
            status=UserStatus.actif,
            must_change_password=False,  # False pour pouvoir se connecter directement sans forcer le changement
        )
        
        session.add(new_admin)
        await session.commit()
        print(f"[+] ✅ Compte admin '{email}' créé avec succès !")
        print(f"    - E-mail/Identifiant : {email}")
        print(f"    - Mot de passe : {password}")
        print(f"    - Rôle : {UserRole.admin.value}")


def main() -> None:
    try:
        asyncio.run(create_admin())
    except Exception as e:
        print(f"[❌] Une erreur est survenue lors de la création du compte : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
