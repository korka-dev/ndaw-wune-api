"""Signalement des erreurs clients (rapport V2, APP-45).

Le tableau de bord et l'application mobile signalent ici leurs erreurs
inattendues (5xx, réseau, plantage d'écran). Chaque signalement reçoit une
référence support (`SUP-…`) affichée à l'utilisateur et journalisée côté
serveur avec la référence de la requête en échec (`X-Request-ID`) : le
support retrouve l'incident sans demander de capture d'écran.

Aucun contenu personnel : seuls des champs techniques bornés sont acceptés,
et tout texte libre est purgé des e-mails, numéros et jetons avant journal.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.rate_limit import limiter
from app.core.security import JWTError, decode_token

router = APIRouter(tags=["Support"])
logger = logging.getLogger("ndawwune.client_errors")

_MOTIFS_SENSIBLES = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"), "<jeton>"),                 # JWT
    (re.compile(r"(?i)(bearer|token|password|mot de passe)\s*[:=]?\s*\S+"), r"\1 <masqué>"),
    (re.compile(r"data:[\w/+.-]+;base64,[\w+/=]+"), "<donnée binaire>"),
    (re.compile(r"\+?\d[\d .-]{7,}\d"), "<numéro>"),                        # téléphones, identifiants longs
]
_REF_REQUETE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def purger(texte: Optional[str], limite: int) -> Optional[str]:
    if not texte:
        return None
    t = texte
    for motif, remplacement in _MOTIFS_SENSIBLES:
        t = motif.sub(remplacement, t)
    t = " ".join(t.split())
    return t[:limite]


class ErreurClient(BaseModel):
    source: Literal["admin", "mobile"]
    app_version: Optional[str] = Field(None, max_length=40)
    plateforme: Optional[str] = Field(None, max_length=40)
    type: Literal["http", "reseau", "ecran", "autre"] = "autre"
    statut_http: Optional[int] = Field(None, ge=0, le=599)
    methode: Optional[str] = Field(None, max_length=10)
    route: Optional[str] = Field(None, max_length=200, description="Chemin d'API ou nom d'écran, sans paramètres")
    message: Optional[str] = Field(None, max_length=500)
    reference_requete: Optional[str] = Field(None, max_length=64, description="X-Request-ID de la requête en échec")
    survenue_le: Optional[datetime] = None


def _auteur(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Attribution sans requête en base : identifiant et rôle du jeton, s'il est valide."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None, None
    try:
        p = decode_token(auth.split(" ", 1)[1])
        return (p.get("sub"), p.get("role")) if p.get("type") == "access" else (None, None)
    except JWTError:
        return None, None


@router.post("/client-errors", status_code=202)
@limiter.limit("30/minute")
async def signaler_erreur(request: Request, body: ErreurClient) -> dict:
    reference = f"SUP-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    user_id, role = _auteur(request)
    ref_req = body.reference_requete if body.reference_requete and _REF_REQUETE.match(body.reference_requete) else None
    route = purger((body.route or "").split("?", 1)[0], 200)
    logger.warning(
        "ERREUR_CLIENT %s source=%s version=%s plateforme=%s type=%s http=%s %s %s requete=%s "
        "utilisateur=%s role=%s survenue=%s message=%s",
        reference, body.source, purger(body.app_version, 40), purger(body.plateforme, 40), body.type,
        body.statut_http, body.methode or "", route or "", ref_req or "—", user_id or "anonyme", role or "—",
        body.survenue_le.isoformat() if body.survenue_le else "—", purger(body.message, 300) or "—",
    )
    return {"reference": reference}
