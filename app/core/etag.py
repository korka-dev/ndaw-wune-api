"""Réponses de synchronisation avec ETag : 304 quand rien n'a changé.

L'app renvoie l'ETag reçu dans `If-None-Match` ; si le contenu est identique
(hors horodatage `synced_at`), le serveur répond 304 sans corps : quelques
octets au lieu du payload complet, et aucune réécriture du cache sur le téléphone.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_CHAMPS_VOLATILS = ("synced_at",)


def etag_de(data: dict) -> str:
    stable = {k: v for k, v in data.items() if k not in _CHAMPS_VOLATILS}
    brut = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(brut.encode()).hexdigest()[:32] + '"'


def reponse_avec_etag(request: Request, payload: BaseModel) -> Response:
    data = payload.model_dump(mode="json")
    etag = etag_de(data)
    entetes = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=entetes)
    return JSONResponse(content=data, headers=entetes)
