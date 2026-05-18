"""
Configuration partagée pour les tests.

Les tests unitaires (sécurité, schémas) n'ont pas besoin de base de données.
On surcharge les settings via des variables d'environnement minimales.
"""
import os
import pytest

# ── Surcharger les settings AVANT l'import de l'application ──────────────────
os.environ.setdefault("SECRET_KEY", "x" * 64)   # ≥ 32 chars requis
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("APP_ENV", "test")
