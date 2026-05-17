from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# ── Moteur async ──────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    # N'activer l'écho SQL qu'en développement : en production c'est du bruit
    # et cela peut exposer des données sensibles dans les logs.
    echo=settings.is_development,
    pool_size=settings.MAX_CONNECTIONS_POOL,
    max_overflow=settings.MAX_OVERFLOW,
    pool_pre_ping=True,           # détecte les connexions mortes avant utilisation
    pool_recycle=settings.POOL_RECYCLE_SECONDS,  # évite les connexions obsolètes
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,       # évite les lazy-load après commit
    autocommit=False,
    autoflush=False,
)


# ── Base déclarative partagée par tous les modèles ────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dépendance FastAPI ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Fournit une session DB transactionnelle par requête.
    Commit automatique en cas de succès, rollback en cas d'exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
