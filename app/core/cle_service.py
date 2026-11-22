"""Authentification par clé de service (lot L6, D9.4).

En-tête `X-Api-Key: nwn_<secret>`. La clé est comparée par empreinte SHA-256 ;
elle doit être active (non révoquée, non expirée) et porter la portée requise.
La dernière utilisation est mise à jour (au plus une fois par minute).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select

from app.core.database import get_db
from app.models.cle_service import CleService

PREFIXE = "nwn_"


def nouvelle_cle() -> tuple[str, str, str]:
    """(valeur complète à montrer une fois, préfixe affichable, empreinte)."""
    valeur = PREFIXE + secrets.token_urlsafe(32)
    return valeur, valeur[:12], empreinte(valeur)


def empreinte(valeur: str) -> str:
    return hashlib.sha256(valeur.encode()).hexdigest()


def exiger_portee(portee: str):
    async def _dep(
        x_api_key: Annotated[Optional[str], Header(alias="X-Api-Key")] = None,
        db=Depends(get_db),
    ) -> CleService:
        refus = HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé de service invalide, expirée ou révoquée.")
        if not x_api_key or not x_api_key.startswith(PREFIXE):
            raise refus
        cle = (await db.execute(select(CleService).where(CleService.empreinte == empreinte(x_api_key)))).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if cle is None or cle.revoquee_le is not None or (cle.expire_le and cle.expire_le <= now):
            raise refus
        if portee not in (cle.portees or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Portée « {portee} » non accordée à cette clé.")
        if cle.derniere_utilisation is None or now - cle.derniere_utilisation > timedelta(minutes=1):
            cle.derniere_utilisation = now
        return cle
    return _dep
