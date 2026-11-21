"""Données analytiques (rapport V2, lot L4 — APP-36 et Partie II D5-D8).

- GET /analytique/indicateurs      indicateurs unifiés (D7), même snapshot que le pack
- GET /analytique/export           archive CSV analytique zippée (D6.1)
- GET /analytique/tables           catalogue des tables (contrat versionné)
- GET /analytique/tables/{table}   API analytique paginée, reprise incrémentale (D6.5)
- GET /analytique/dictionnaire     dictionnaire des variables (D6.4)
- GET /analytique/exports          historique des exports (centre d'exports, D9.2)

Accès contrôlé côté serveur (D6.2, D11) : administrateurs et coordonnateurs
(l'évaluateur consulte en lecture). Le mode NOMINATIF (noms, textes libres,
identifiants réels des élèves) est réservé au rôle admin et chaque export est
journalisé. En attendant la matrice des rôles validée par ARED (APP-02).
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.core.deps import AdminUser, DB
from app.models.audit_log import AuditLog
from app.models.user import User, UserRole
from app.services import analytique_indicateurs as indic
from app.services.analytique_pack import construire_zip, dictionnaire, snapshot
from app.services.analytique_tables import SCHEMA_VERSION, TABLES, TABLES_PAR_NOM, Contexte, Filtres

router = APIRouter(prefix="/analytique", tags=["Admin — Données analytiques"])

STATUTS = ("tous", "valides", "soumis", "corrige", "annule")


def filtres(
    campagne_id: Optional[uuid.UUID] = None,
    date_debut: Optional[date] = Query(None, description="Date d'activité minimale (AAAA-MM-JJ)"),
    date_fin: Optional[date] = Query(None, description="Date d'activité maximale"),
    recu_depuis: Optional[datetime] = Query(None, description="Réception serveur minimale (données tardives)"),
    recu_jusqua: Optional[datetime] = None,
    ief: Optional[str] = None,
    commune: Optional[str] = None,
    ecole_id: Optional[uuid.UUID] = None,
    tuteur_id: Optional[uuid.UUID] = None,
    superviseur_id: Optional[uuid.UUID] = None,
    statut: str = Query("tous", description="tous | valides | soumis | corrige | annule"),
    nominatif: bool = False,
) -> Filtres:
    if statut not in STATUTS:
        raise HTTPException(422, f"statut invalide (attendu : {', '.join(STATUTS)}).")
    if date_debut and date_fin and date_fin < date_debut:
        raise HTTPException(422, "date_fin antérieure à date_debut.")
    return Filtres(campagne_id, date_debut, date_fin, recu_depuis, recu_jusqua, ief, commune, ecole_id,
                   tuteur_id, superviseur_id, statut, nominatif)


FiltresDep = Annotated[Filtres, Depends(filtres)]


def _autoriser_nominatif(user: User, f: Filtres) -> None:
    if f.nominatif and user.role != UserRole.admin:
        raise HTTPException(403, "L'export nominatif est réservé aux administrateurs.")


def _journaliser(db: DB, user: User, path: str, contenu: dict) -> None:
    db.add(AuditLog(
        user_id=user.id, user_name=user.name, user_role=user.role.value, action="export",
        entity="Analytique", method="GET", path=path,
        description=json.dumps(contenu, ensure_ascii=False, default=str),
    ))


@router.get("/indicateurs")
async def indicateurs(f: FiltresDep, user: AdminUser) -> dict:
    """Indicateurs D7 calculés depuis le même snapshot que l'archive (REC16).
    Le mode nominatif n'a pas d'effet sur les valeurs."""
    f.nominatif = False
    tables, _, borne = await snapshot(f)
    return {
        "schema_version": SCHEMA_VERSION, "borne_snapshot": borne.isoformat(),
        "fraicheur": indic.fraicheur(tables), "filtres": f.en_dict(),
        "indicateurs": indic.calculer(tables),
    }


@router.get("/export")
async def export_pack(f: FiltresDep, user: AdminUser, db: DB) -> Response:
    """Archive CSV analytique complète du périmètre filtré (aucun plafond
    d'écran : toutes les lignes éligibles). La réponse n'est envoyée qu'une
    fois l'archive entièrement produite."""
    _autoriser_nominatif(user, f)
    export_id = f"{'NOMIN' if f.nominatif else 'PSEUDO'}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    tables, ctx, borne = await snapshot(f)
    contenu, manifeste = construire_zip(tables, ctx, borne, export_id)
    _journaliser(db, user, "/admin/analytique/export", {
        "export_id": export_id, "mode": manifeste["mode"], "filtres": manifeste["filtres"],
        "borne_snapshot": manifeste["borne_snapshot"],
        "lignes": {x["table"]: x["lignes"] for x in manifeste["fichiers"] if x["fichier"].startswith("tables/")},
        "anomalies": manifeste["bilan_qualite"]["anomalies"], "taille_octets": len(contenu),
    })
    nom = f"ndawwune_analytique_{export_id}.zip"
    return Response(content=contenu, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{nom}"', "X-Export-Id": export_id,
        "Access-Control-Expose-Headers": "X-Export-Id, Content-Disposition",
    })


@router.get("/tables")
async def catalogue(_: AdminUser, nominatif: bool = False) -> dict:
    return {"schema_version": SCHEMA_VERSION, "tables": [{
        "table": t.nom, "unite": t.unite, "cle": t.cle, "statut": t.statut, "note": t.note or None,
        "reprise_incrementale": t.builder is not None and t.nom != "dictionnaire_codes",
        "colonnes": [{"nom": c.nom, "type": c.type, "definition": c.definition, "nature": c.nature,
                      "valeurs": c.valeurs or None} for c in t.colonnes_pour(nominatif)],
    } for t in TABLES]}


@router.get("/dictionnaire")
async def get_dictionnaire(_: AdminUser, nominatif: bool = False) -> list[dict]:
    return dictionnaire(nominatif)


def _curseur(valeur: str) -> tuple[str, str]:
    try:
        maj, cle = json.loads(base64.urlsafe_b64decode(valeur.encode()).decode())
        return str(maj), str(cle)
    except Exception:
        raise HTTPException(422, "curseur illisible.")


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


@router.get("/tables/{table}")
async def lire_table(
    table: str, f: FiltresDep, user: AdminUser,
    updated_since: Optional[datetime] = Query(None, description="Reprise : lignes modifiées depuis (inclus)"),
    curseur: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    """Une table analytique, paginée par clé de reprise déterministe
    (horodatage de modification, clé). Les annulations sont incluses (statut
    de l'enregistrement) : un incrément les transmet (REC14). Pour les tables
    dérivées d'un rapport, la reprise suit la modification du rapport."""
    t = TABLES_PAR_NOM.get(table)
    if t is None:
        raise HTTPException(404, "Table inconnue.")
    _autoriser_nominatif(user, f)
    if updated_since and (t.builder is None or t.nom == "dictionnaire_codes"):
        raise HTTPException(422, "Cette table ne se lit pas en reprise incrémentale : utilisez un snapshot complet.")
    async with AsyncSessionLocal() as s:
        await s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await s.execute(text("SET TRANSACTION READ ONLY"))
        borne = (await s.execute(text("SELECT now()"))).scalar_one()
        ctx = Contexte(s, f)
        lignes = await t.builder(ctx) if t.builder else []
        maj_rapport = {}
        if t.maj is None and lignes and "rapport_id" in lignes[0]:
            maj_rapport = {r["rapport_id"]: r["updated_at"] for r in await TABLES_PAR_NOM["rapports_journaliers"].builder(ctx)}
        await s.rollback()

    cle_nom = t.cle if "+" not in t.cle else None
    colonnes = [c.nom for c in t.colonnes_pour(f.nominatif)]

    def maj(l: dict) -> str:
        v = l.get(t.maj) if t.maj else maj_rapport.get(l.get("rapport_id"))
        return _jsonable(v) or ""

    def cle(l: dict) -> str:
        return str(l.get(cle_nom)) if cle_nom else "|".join(str(l.get(k)) for k in t.cle.split("+"))

    if updated_since:
        seuil = updated_since if updated_since.tzinfo else updated_since.replace(tzinfo=timezone.utc)
        lignes = [l for l in lignes if maj(l) and datetime.fromisoformat(maj(l)) >= seuil]
    lignes.sort(key=lambda l: (maj(l), cle(l)))
    if curseur:
        dernier = _curseur(curseur)
        lignes = [l for l in lignes if (maj(l), cle(l)) > dernier]
    page, reste = lignes[:limit], lignes[limit:]
    suivant = None
    if reste and page:
        suivant = base64.urlsafe_b64encode(json.dumps([maj(page[-1]), cle(page[-1])]).encode()).decode()
    return {
        "table": t.nom, "schema_version": SCHEMA_VERSION, "statut": t.statut, "borne_snapshot": borne.isoformat(),
        "colonnes": colonnes, "items": [{c: _jsonable(l.get(c)) for c in colonnes} for l in page],
        "curseur_suivant": suivant, "restant": len(reste),
    }


@router.get("/exports")
async def historique_exports(db: DB, _: AdminUser, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.entity == "Analytique", AuditLog.action == "export")
        .order_by(AuditLog.created_at.desc()).limit(limit)
    )).scalars().all()
    out = []
    for a in rows:
        try:
            d = json.loads(a.description)
        except ValueError:
            d = {}
        out.append({"date": a.created_at.isoformat(), "par": a.user_name, "role": a.user_role, **d})
    return out
