from __future__ import annotations

from functools import cached_property
from typing import List, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "ARED-NdawWune"
    APP_ENV: Literal["development", "production", "test"] = "development"

    # ── Stockage des fichiers uploadés ────────────────────────────────────────
    # En développement : ./uploads (relatif au répertoire de travail)
    # En production VPS : /var/www/ndawwune/uploads (ou tout chemin monté)
    UPLOADS_DIR: str = "./uploads"

    # ── Sécurité JWT ──────────────────────────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60      # 1 h (anciennement 24 h — réduit pour la sécurité)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30        # 30 jours (inchangé)

    # ── Base de données ───────────────────────────────────────────────────────
    DATABASE_URL: str
    MAX_CONNECTIONS_POOL: int = 20
    MAX_OVERFLOW: int = 10
    POOL_RECYCLE_SECONDS: int = 1800

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str
    REDIS_MAX_CONNECTIONS: int = 50
    SYNC_CACHE_TTL_SECONDS: int = 3600

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Pagination ────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 10000

    # ── Mot de passe par défaut (comptes créés par l'admin) ───────────────────
    # Tous les nouveaux comptes reçoivent ce mot de passe temporaire.
    # L'utilisateur est forcé de le changer à la première connexion.
    DEFAULT_USER_PASSWORD: str = "P@sser123"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",          # ignore les variables non déclarées dans .env
    )

    # ── Propriétés calculées ──────────────────────────────────────────────────

    @cached_property
    def cors_origins_list(self) -> List[str]:
        """
        Origines autorisées par le middleware CORS.

        Production : uniquement le dashboard admin (https://admin.ndawwune.cloud).
          L'app mobile est une application native React Native — elle ne passe pas
          par le mécanisme CORS du navigateur et n'a donc pas besoin d'être listée.

        Développement : localhost + origines supplémentaires déclarées dans CORS_ORIGINS.
        """
        if self.is_production:
            return ["https://admin.ndawwune.cloud"]

        # Développement : origines locales par défaut
        origins = [
            "http://localhost:3000",
            "http://localhost:8081",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:8081",
        ]
        # Fusionner avec les origines déclarées dans le .env (ex : IP Wi-Fi locale)
        env_origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
        for o in env_origins:
            if o not in origins:
                origins.append(o)
        return origins

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def docs_url(self) -> str | None:
        """Désactive Swagger en production."""
        return None if self.is_production else "/docs"

    @property
    def redoc_url(self) -> str | None:
        """Désactive ReDoc en production."""
        return None if self.is_production else "/redoc"

    @property
    def openapi_url(self) -> str | None:
        """Désactive le schéma OpenAPI en production."""
        return None if self.is_production else "/openapi.json"

    # ── Validateurs ───────────────────────────────────────────────────────────

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_strength(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY doit contenir au moins 32 caractères. "
                "Générez-en un avec : python -c \"import secrets; print(secrets.token_hex(64))\""
            )
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def database_url_must_be_async(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL doit utiliser le driver asyncpg "
                "(postgresql+asyncpg://...)"
            )
        return v


settings = Settings()
