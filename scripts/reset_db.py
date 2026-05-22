#!/usr/bin/env python3
"""
NDAW WUNE — Script de réinitialisation de la base de données (Purge)
==================================================================
Supprime toutes les données de production (élèves, écoles, classes, enseignants, 
sessions, rapports, séances) tout en conservant les comptes administrateurs.

Usage :
    python scripts/reset_db.py
"""

import asyncio
import os
import sys
from pathlib import Path

# ── Rendre "app" importable ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── Forcer DATABASE_URL vers localhost AVANT tout import de app.core ─────────
_DEFAULT_DB = "postgresql+asyncpg://ared_user:ared_secret@localhost:5432/ared_ndawune"
if not os.path.exists("/.dockerenv") and ("DATABASE_URL" not in os.environ or "://db:" in os.environ.get("DATABASE_URL", "")):
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", _DEFAULT_DB).replace(
        "@db:", "@localhost:"
    )
    if os.environ["DATABASE_URL"].startswith("postgresql+asyncpg://$"):
        os.environ["DATABASE_URL"] = _DEFAULT_DB

from sqlalchemy import delete
from app.core.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.eleve import Eleve
from app.models.seance import Seance
from app.models.rapport_journalier import RapportJournalier
from app.models.document import Document
from app.models.planning import Planning
from app.models.session import Session


async def reset_db() -> None:
    print("\n⚠️  ATTENTION : Cette action va supprimer définitivement :")
    print("   - Tous les élèves")
    # Remplacez la saisie interactive classique par une détection ou invite sûre
    print("   - Toutes les écoles et classes")
    print("   - Tous les comptes (Enseignants, Superviseurs, Évaluateurs)")
    print("   - Toutes les séances, sessions et rapports de suivi")
    print("   🛡️  SEULS les comptes ADMINISTRATEURS seront conservés.\n")
    
    # Demander confirmation s'il s'agit d'une exécution interactive
    if sys.stdin.isatty():
        confirm = input("👉 Tapez 'OUI' pour confirmer la suppression totale : ")
        if confirm.strip() != "OUI":
            print("❌ Opération annulée par l'utilisateur.")
            sys.exit(0)

    print("\n[*] Connexion à la base de données...")
    async with AsyncSessionLocal() as session:
        try:
            # 1. Supprimer les rapports journaliers
            res = await session.execute(delete(RapportJournalier))
            print(f"  ✓ {res.rowcount} rapports journaliers supprimés.")
            
            # 2. Supprimer les séances
            res = await session.execute(delete(Seance))
            print(f"  ✓ {res.rowcount} séances supprimées.")
            
            # 3. Supprimer les élèves
            res = await session.execute(delete(Eleve))
            print(f"  ✓ {res.rowcount} élèves supprimés.")
            
            # 4. Supprimer les documents
            res = await session.execute(delete(Document))
            print(f"  ✓ {res.rowcount} documents supprimés.")
            
            # 5. Supprimer les plannings
            res = await session.execute(delete(Planning))
            print(f"  ✓ {res.rowcount} plannings supprimés.")
            
            # 6. Supprimer les classes
            res = await session.execute(delete(SchoolClasse))
            print(f"  ✓ {res.rowcount} classes supprimées.")
            
            # 7. Supprimer tous les utilisateurs sauf les administrateurs
            res = await session.execute(delete(User).where(User.role != UserRole.admin))
            print(f"  ✓ {res.rowcount} utilisateurs (Enseignants, Superviseurs, Évaluateurs) supprimés.")
            
            # 8. Supprimer les écoles
            res = await session.execute(delete(School))
            print(f"  ✓ {res.rowcount} écoles supprimées.")
            
            # 9. Supprimer les sessions de programme
            res = await session.execute(delete(Session))
            print(f"  ✓ {res.rowcount} sessions de programme supprimées.")
            
            await session.commit()
            print("\n[+] ✅ base de données réinitialisée avec succès !")
            print("[+] Seuls les comptes administrateurs sont actifs en base.")
            
        except Exception as e:
            await session.rollback()
            print(f"\n❌ Erreur lors de la réinitialisation (Rollback effectué) : {e}")
            sys.exit(1)


def main() -> None:
    try:
        asyncio.run(reset_db())
    except KeyboardInterrupt:
        print("\n❌ Opération interrompue.")
        sys.exit(1)


if __name__ == "__main__":
    main()
