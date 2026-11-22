"""Routes admin — suivi de la reprise de l'historique (lot L6, D10.2).

L'import lui-même se lance avec `python -m scripts.reprise_historique` sur une
copie figée des sources. L'admin consulte les lots, leur réconciliation et les
lignes en quarantaine, et tient la table de correspondance (validation humaine).

  GET  /admin/reprise/lots
  GET  /admin/reprise/lots/{id}
  GET  /admin/reprise/lots/{id}/lignes?statut=quarantaine
  GET  /admin/reprise/correspondances
  POST /admin/reprise/correspondances          validation d'une correspondance
  POST /admin/reprise/correspondances/import   CSV type;code_source;cible_id[;libelle_source;commentaire]
"""
from __future__ import annotations

import csv
import io
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.deps import DB, AdminUser
from app.core.pagination import Page, Pagination
from app.models.eleve import Eleve
from app.models.reprise import TYPES_CORRESPONDANCE, CorrespondanceReprise, LotReprise, RepriseLigne
from app.models.school import School
from app.models.user import User
from app.services.audit_service import journaliser

router = APIRouter(prefix="/reprise", tags=["Admin — Reprise de l'historique"])


class CorrespondanceIn(BaseModel):
    type: str = Field(pattern="^(tuteur|superviseur|ecole|eleve)$")
    code_source: str = Field(min_length=1, max_length=255)
    cible_id: uuid.UUID
    libelle_source: Optional[str] = Field(default=None, max_length=255)
    commentaire: Optional[str] = Field(default=None, max_length=2000)


def _lot(l: LotReprise) -> dict:
    return {"id": l.id, "nom": l.nom, "session_id": l.session_id, "sources": l.sources, "statut": l.statut,
            "compteurs": l.compteurs, "reconciliation": l.reconciliation, "commentaire": l.commentaire,
            "cree_par": l.cree_par, "created_at": l.created_at, "importe_le": l.importe_le}


@router.get("/lots")
async def lots(db: DB, _: AdminUser) -> list[dict]:
    return [{k: v for k, v in _lot(l).items() if k != "reconciliation"}
            for l in (await db.execute(select(LotReprise).order_by(LotReprise.created_at.desc()))).scalars()]


@router.get("/lots/{lot_id}")
async def lot(lot_id: uuid.UUID, db: DB, _: AdminUser) -> dict:
    l = (await db.execute(select(LotReprise).where(LotReprise.id == lot_id))).scalar_one_or_none()
    if l is None:
        raise HTTPException(404, "Lot introuvable.")
    return _lot(l)


@router.get("/lots/{lot_id}/lignes")
async def lignes(lot_id: uuid.UUID, db: DB, _: AdminUser, page: Pagination,
                 statut: Optional[str] = None, fichier: Optional[str] = None) -> Page[dict]:
    q = select(RepriseLigne).where(RepriseLigne.lot_id == lot_id)
    if statut:
        q = q.where(RepriseLigne.statut == statut)
    if fichier:
        q = q.where(RepriseLigne.fichier == fichier)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(RepriseLigne.fichier, RepriseLigne.numero_ligne)
                             .offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=[{
        "id": r.id, "fichier": r.fichier, "numero_ligne": r.numero_ligne, "source_submission_id": r.source_submission_id,
        "source_parent_id": r.source_parent_id, "statut": r.statut, "motif": r.motif, "cible_type": r.cible_type,
        "cible_id": r.cible_id, "donnees": r.donnees,
    } for r in rows])


@router.get("/correspondances")
async def lister_correspondances(db: DB, _: AdminUser, type: Optional[str] = None) -> list[dict]:
    q = select(CorrespondanceReprise).order_by(CorrespondanceReprise.type, CorrespondanceReprise.code_source)
    if type:
        q = q.where(CorrespondanceReprise.type == type)
    return [{"id": c.id, "type": c.type, "code_source": c.code_source, "libelle_source": c.libelle_source,
             "cible_id": c.cible_id, "valide_par": c.valide_par, "commentaire": c.commentaire,
             "updated_at": c.updated_at} for c in (await db.execute(q)).scalars()]


async def _cible_existe(db, type_: str, cible_id: uuid.UUID) -> bool:
    modele = {"tuteur": User, "superviseur": User, "ecole": School, "eleve": Eleve}[type_]
    return (await db.execute(select(modele.id).where(modele.id == cible_id))).first() is not None


async def _enregistrer(db, user, c: CorrespondanceIn) -> None:
    if not await _cible_existe(db, c.type, c.cible_id):
        raise HTTPException(422, f"{c.type} « {c.code_source} » : identifiant cible inconnu ({c.cible_id}).")
    stmt = insert(CorrespondanceReprise).values(
        id=uuid.uuid4(), type=c.type, code_source=c.code_source.strip(), libelle_source=c.libelle_source,
        cible_id=c.cible_id, valide_par=user.name, commentaire=c.commentaire)
    await db.execute(stmt.on_conflict_do_update(
        constraint="uq_correspondances_reprise_type_code",
        set_={"cible_id": c.cible_id, "libelle_source": c.libelle_source, "valide_par": user.name,
              "commentaire": c.commentaire, "updated_at": func.now()}))


@router.post("/correspondances")
async def valider(body: CorrespondanceIn, db: DB, user: AdminUser) -> dict:
    await _enregistrer(db, user, body)
    journaliser(db, acteur=user, action="validation", entity="Reprise de l'historique",
                description=f"Correspondance {body.type} « {body.code_source} » → {body.cible_id}",
                cible_type="correspondance_reprise", path="/reprise/correspondances")
    return {"ok": True}


@router.post("/correspondances/import")
async def importer_correspondances(db: DB, user: AdminUser, file: UploadFile = File(...)) -> dict:
    contenu = (await file.read(2_000_001)).decode("utf-8-sig", errors="replace")
    if len(contenu) > 2_000_000:
        raise HTTPException(413, "Fichier trop volumineux (2 Mo max).")
    delim = ";" if contenu[:2000].count(";") >= contenu[:2000].count(",") else ","
    erreurs, ok = [], 0
    for n, l in enumerate(csv.DictReader(io.StringIO(contenu), delimiter=delim), start=2):
        try:
            c = CorrespondanceIn(type=(l.get("type") or "").strip(), code_source=(l.get("code_source") or "").strip(),
                                 cible_id=(l.get("cible_id") or "").strip(),
                                 libelle_source=(l.get("libelle_source") or None), commentaire=(l.get("commentaire") or None))
            async with db.begin_nested():
                await _enregistrer(db, user, c)
            ok += 1
        except Exception as e:  # ligne par ligne : une erreur n'arrête pas les autres
            erreurs.append({"ligne": n, "erreur": getattr(e, "detail", None) or str(e)[:300]})
    journaliser(db, acteur=user, action="import", entity="Reprise de l'historique",
                description=f"Import de correspondances : {ok} validée(s), {len(erreurs)} erreur(s)",
                cible_type="correspondance_reprise", path="/reprise/correspondances/import")
    return {"validees": ok, "erreurs": erreurs[:200], "nb_erreurs": len(erreurs), "types": list(TYPES_CORRESPONDANCE)}
