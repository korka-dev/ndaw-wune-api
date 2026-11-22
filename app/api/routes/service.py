"""Accès de service en lecture seule (lot L6, D9.4 « actualisation du dashboard par API »).

Authentification : en-tête `X-Api-Key` (clé créée dans l'admin). Données
pseudonymisées uniquement : aucun nom, téléphone, commentaire ni média ; les
identifiants opaques permettent les jointures (D11).

  GET /service/analytique/indicateurs
  GET /service/analytique/tables
  GET /service/analytique/tables/{table}    pagination incrémentale (comme l'admin)
  GET /service/analytique/export            archive ZIP pseudonymisée
  GET /service/tableau-de-bord/{vue}        vue-ensemble | soumissions | assiduite | supervision | appui
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.api.routes.admin import analytique as admin_analytique
from app.api.routes.admin.tableau_bord import FiltresDep as FiltresTbDep
from app.core.cle_service import exiger_portee
from app.core.deps import DB
from app.models.cle_service import CleService
from app.services import tableau_bord as tb
from app.services.analytique_pack import construire_zip, snapshot
from app.services.audit_service import journaliser

router = APIRouter(prefix="/service", tags=["Service — lecture seule (clé d'API)"])

CleAnalytique = Annotated[CleService, Depends(exiger_portee("analytique"))]
CleTableau = Annotated[CleService, Depends(exiger_portee("tableau_bord"))]

CHAMPS_NOMINATIFS = {"acteur", "nom", "tuteur", "dernier_superviseur", "ecole", "classes"}


@router.get("/analytique/indicateurs")
async def indicateurs(f: admin_analytique.FiltresDep, _: CleAnalytique) -> dict:
    return await admin_analytique.indicateurs(f, None)


@router.get("/analytique/tables")
async def catalogue(_: CleAnalytique) -> dict:
    return await admin_analytique.catalogue(None, nominatif=False)


@router.get("/analytique/tables/{table}")
async def lire_table(
    table: str, f: admin_analytique.FiltresDep, _: CleAnalytique,
    updated_since: Optional[datetime] = None, curseur: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    f.nominatif = False
    return await admin_analytique.lire_table(table, f, None, updated_since, curseur, limit)


@router.get("/analytique/export")
async def export_pack(f: admin_analytique.FiltresDep, cle: CleAnalytique, db: DB) -> Response:
    f.nominatif = False
    export_id = f"SERVICE-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    tables, ctx, borne = await snapshot(f)
    contenu, manifeste = construire_zip(tables, ctx, borne, export_id)
    journaliser(db, acteur=None, action="export", entity="Analytique", method="GET",
                path="/service/analytique/export", cible_type="cle_service", cible_id=cle.id,
                description=f"Export pseudonymisé {export_id} par la clé « {cle.nom} » ({len(contenu)} octets)")
    return Response(content=contenu, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="ndawwune_analytique_{export_id}.zip"', "X-Export-Id": export_id,
    })


def _pseudonymiser(vue: dict) -> dict:
    vue = dict(vue)
    vue["lignes"] = [{k: v for k, v in l.items() if k not in CHAMPS_NOMINATIFS} for l in vue.get("lignes", [])]
    return vue


@router.get("/tableau-de-bord/{vue}")
async def tableau(vue: str, f: FiltresTbDep, db: DB, _: CleTableau) -> dict:
    fonctions = {"vue-ensemble": tb.vue_ensemble, "soumissions": tb.soumissions, "assiduite": tb.assiduite,
                 "supervision": tb.supervision, "appui": tb.appui}
    fn = fonctions.get(vue)
    if fn is None:
        raise HTTPException(404, f"Vue inconnue (attendu : {', '.join(fonctions)}).")
    return _pseudonymiser(await fn(db, f))
