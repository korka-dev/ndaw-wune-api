from __future__ import annotations

import hashlib
from urllib.parse import quote, urlsplit, urlunsplit
from functools import cached_property
from typing import List, Literal

from pydantic import field_validator
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
    # Rotation du refresh token (APP-10) : un refresh token déjà échangé reste
    # accepté pendant ce délai, pour qu'une réponse perdue sur réseau faible
    # (ou deux onglets) ne déconnecte pas l'utilisateur. Au-delà : refusé.
    REFRESH_ROTATION_GRACE_SECONDS: int = 60

    # ── Base de données ───────────────────────────────────────────────────────
    DATABASE_URL: str
    MAX_CONNECTIONS_POOL: int = 20
    MAX_OVERFLOW: int = 10
    POOL_RECYCLE_SECONDS: int = 1800

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str
    # Mot de passe Redis transmis à part (APP-43) : il est encodé dans l'URL
    # effective (settings.redis_url), donc « @ », « : » ou « / » ne la cassent plus.
    REDIS_PASSWORD: str = ""
    REDIS_MAX_CONNECTIONS: int = 50
    SYNC_CACHE_TTL_SECONDS: int = 3600

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Limitation de débit (APP-11) ──────────────────────────────────────────
    # Limite par utilisateur authentifié (sinon par IP) sur toutes les routes.
    # Une synchronisation mobile complète fait quelques dizaines de requêtes.
    RATE_LIMIT_DEFAULT: str = "300/minute"
    RATE_LIMIT_REFRESH: str = "30/minute"

    # ── Compatibilité mobile (APP-46) ─────────────────────────────────────────
    # Version minimale de l'app acceptée sur /api/v1/app/* (vide = aucune).
    # Intervalle de la clôture automatique des séances oubliées (APP-21).
    # 0 = désactivée (tests).
    SEANCE_CLEANUP_INTERVAL_SECONDS: int = 900
    MIN_APP_VERSION: str = ""
    # Dernière version publiée (information affichée par l'app).
    LATEST_APP_VERSION: str = ""

    # ── Pagination ────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 10000

    # ── Mot de passe par défaut (comptes créés par l'admin) ───────────────────
    # Tous les nouveaux comptes reçoivent ce mot de passe temporaire.
    # L'utilisateur est forcé de le changer à la première connexion.
    DEFAULT_USER_PASSWORD: str = "Passer123"

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

        Production : uniquement le(s) dashboard(s) admin déclarés dans CORS_ORIGINS
          (ex : https://admin.ndawwune.cloud) — "*" y est ignoré. Par défaut, si
          CORS_ORIGINS est vide : https://admin.ndawwune.cloud.
          L'app mobile est une application native React Native — elle ne passe pas
          par le mécanisme CORS du navigateur et n'a donc pas besoin d'être listée.

        Développement : localhost + origines supplémentaires déclarées dans CORS_ORIGINS.
        """
        if self.is_production:
            origins = [
                o.strip() for o in self.CORS_ORIGINS.split(",")
                if o.strip().startswith("https://")
            ]
            if not origins:
                # APP-42 : repli historique, signalé — sur le serveur ARED,
                # CORS_ORIGINS doit nommer explicitement le domaine du dashboard.
                import logging
                logging.getLogger(__name__).warning(
                    "CORS_ORIGINS vide ou sans origine https : repli sur https://admin.ndawwune.cloud.")
                return ["https://admin.ndawwune.cloud"]
            return origins

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

    @cached_property
    def redis_url(self) -> str:
        """REDIS_URL avec REDIS_PASSWORD encodé (remplace un éventuel userinfo)."""
        if not self.REDIS_PASSWORD:
            return self.REDIS_URL
        parts = urlsplit(self.REDIS_URL)
        host = parts.netloc.rsplit("@", 1)[-1]
        netloc = f":{quote(self.REDIS_PASSWORD, safe='')}@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def secret_key_is_compromised(self) -> bool:
        """
        Vrai si SECRET_KEY est une clé publiée dans l'historique git (APP-04).
        On compare des empreintes SHA-256, jamais les valeurs. Une telle clé
        permet de forger des jetons : elle ne doit servir qu'en développement.
        """
        return hashlib.sha256(self.SECRET_KEY.encode()).hexdigest() in _COMPROMISED_SECRET_KEY_SHA256

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


# Empreintes SHA-256 de clés ayant été versionnées (ex. ancien .env.dev, dont le
# commentaire indiquait « même clé que prod »). Ajouter ici toute clé exposée.
_COMPROMISED_SECRET_KEY_SHA256 = frozenset({
    "fad69ec5894922d44b9a3d7c75680773f9c964fcb65144ca0a74cd8dd084697f",
})

settings = Settings()
