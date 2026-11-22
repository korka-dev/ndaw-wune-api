"""Routes admin — grilles de visite versionnées et visites pédagogiques (lot L6, D9.3).

Grilles :
  GET    /admin/grilles-visite                     liste (toutes versions)
  POST   /admin/grilles-visite                     nouvelle grille (brouillon)
  GET    /admin/grilles-visite/{id}
  PUT    /admin/grilles-visite/{id}                modifier un BROUILLON
  POST   /admin/grilles-visite/{id}/publier        brouillon → active (figée)
  POST   /admin/grilles-visite/{id}/nouvelle-version   copie en brouillon (version + 1)
  POST   /admin/grilles-visite/{id}/retirer        active → retiree (motif)
Visites :
  GET    /admin/visites                            liste filtrée et paginée
  GET    /admin/visites/export                     CSV (une ligne par réponse)
  GET    /admin/visites/{id}
  POST   /admin/visites/{id}/annuler               annulation motivée
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select

from app.core.deps import DB, AdminUser
from app.core.export_utils import sanitize_cell as safe_cell
from app.core.pagination import Page, Pagination
from app.models.school import School
from app.models.user import User
from app.models.visite import GrilleVisite, GrilleVisiteItem, Visite, VisiteReponse
from app.schemas.visite import GrilleIn, GrilleOut, MotifIn, VisiteOut
from app.services import visite_service
from app.services.audit_service import journaliser

router = APIRouter(tags=["Admin — Visites pédagogiques"])


async def _grille(db, grille_id: uuid.UUID, verrou: bool = False) -> GrilleVisite:
    q = select(GrilleVisite).where(GrilleVisite.id == grille_id)
    if verrou:
        q = q.with_for_update()
    g = (await db.execute(q)).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, "Grille introuvable.")
    return g


def _items(g: GrilleVisite, body: GrilleIn) -> list[GrilleVisiteItem]:
    return [GrilleVisiteItem(grille_id=g.id, **i.model_dump()) for i in body.items]


@router.get("/grilles-visite", response_model=list[GrilleOut])
async def lister_grilles(db: DB, _: AdminUser) -> list[GrilleOut]:
    grilles = (await db.execute(select(GrilleVisite).order_by(GrilleVisite.nom, GrilleVisite.version.desc()))).scalars().all()
    return [await visite_service.grille_out(db, g) for g in grilles]


@router.post("/grilles-visite", response_model=GrilleOut, status_code=201)
async def creer_grille(body: GrilleIn, db: DB, user: AdminUser) -> GrilleOut:
    derniere = (await db.execute(select(func.max(GrilleVisite.version)).where(GrilleVisite.nom == body.nom))).scalar_one()
    g = GrilleVisite(nom=body.nom, description=body.description, session_id=body.session_id,
                     version=(derniere or 0) + 1, statut="brouillon")
    db.add(g)
    await db.flush()
    db.add_all(_items(g, body))
    await db.flush()
    return await visite_service.grille_out(db, g)


@router.get("/grilles-visite/{grille_id}", response_model=GrilleOut)
async def lire_grille(grille_id: uuid.UUID, db: DB, _: AdminUser) -> GrilleOut:
    return await visite_service.grille_out(db, await _grille(db, grille_id))


@router.put("/grilles-visite/{grille_id}", response_model=GrilleOut)
async def modifier_grille(grille_id: uuid.UUID, body: GrilleIn, db: DB, _: AdminUser) -> GrilleOut:
    g = await _grille(db, grille_id, verrou=True)
    if g.statut != "brouillon":
        raise HTTPException(409, {"code": "GRILLE_FIGEE",
                                  "message": "Une grille publiée n'est plus modifiable : créez une nouvelle version."})
    g.nom, g.description, g.session_id = body.nom, body.description, body.session_id
    await db.execute(delete(GrilleVisiteItem).where(GrilleVisiteItem.grille_id == g.id))
    db.add_all(_items(g, body))
    await db.flush()
    return await visite_service.grille_out(db, g)


@router.post("/grilles-visite/{grille_id}/publier", response_model=GrilleOut)
async def publier_grille(grille_id: uuid.UUID, db: DB, user: AdminUser) -> GrilleOut:
    g = await _grille(db, grille_id, verrou=True)
    if g.statut != "brouillon":
        raise HTTPException(409, "Seul un brouillon peut être publié.")
    items = await visite_service.items_de(db, g.id)
    if not [i for i in items if i.type != "texte"]:
        raise HTTPException(422, "Une grille doit contenir au moins un item noté ou un outil.")
    # Une seule version active par nom et par périmètre : la précédente est retirée.
    for ancienne in (await db.execute(select(GrilleVisite).where(
        GrilleVisite.nom == g.nom, GrilleVisite.statut == "active",
        GrilleVisite.session_id.is_not_distinct_from(g.session_id),
    ))).scalars():
        ancienne.statut = "retiree"
    g.statut = "active"
    g.publiee_le = datetime.now(timezone.utc)
    journaliser(db, acteur=user, action="publication", entity="Grilles de visite",
                description=f"Publication de « {g.nom} » v{g.version} ({len(items)} items)",
                cible_type="grille_visite", cible_id=g.id, path=f"/grilles-visite/{g.id}/publier")
    await db.flush()
    return await visite_service.grille_out(db, g)


@router.post("/grilles-visite/{grille_id}/nouvelle-version", response_model=GrilleOut, status_code=201)
async def nouvelle_version(grille_id: uuid.UUID, db: DB, _: AdminUser) -> GrilleOut:
    src = await _grille(db, grille_id)
    derniere = (await db.execute(select(func.max(GrilleVisite.version)).where(GrilleVisite.nom == src.nom))).scalar_one()
    g = GrilleVisite(nom=src.nom, description=src.description, session_id=src.session_id,
                     version=derniere + 1, statut="brouillon")
    db.add(g)
    await db.flush()
    for i in await visite_service.items_de(db, src.id):
        db.add(GrilleVisiteItem(grille_id=g.id, code=i.code, domaine=i.domaine, libelle=i.libelle, type=i.type,
                                bareme_max=i.bareme_max, ordre=i.ordre, aide=i.aide, obligatoire=i.obligatoire))
    await db.flush()
    return await visite_service.grille_out(db, g)


@router.post("/grilles-visite/{grille_id}/retirer", response_model=GrilleOut)
async def retirer_grille(grille_id: uuid.UUID, body: MotifIn, db: DB, user: AdminUser) -> GrilleOut:
    g = await _grille(db, grille_id, verrou=True)
    if g.statut != "active":
        raise HTTPException(409, "Seule une grille active peut être retirée.")
    g.statut = "retiree"
    journaliser(db, acteur=user, action="retrait", entity="Grilles de visite",
                description=f"Retrait de « {g.nom} » v{g.version}", cible_type="grille_visite",
                cible_id=g.id, motif=body.motif, path=f"/grilles-visite/{g.id}/retirer")
    await db.flush()
    return await visite_service.grille_out(db, g)


# ── Visites ───────────────────────────────────────────────────────────────────

def _filtres(q, session_id, school_id, superviseur_id, teacher_id, date_debut, date_fin, statut):
    if session_id:
        q = q.where(Visite.session_id == session_id)
    if school_id:
        q = q.where(Visite.school_id == school_id)
    if superviseur_id:
        q = q.where(Visite.superviseur_id == superviseur_id)
    if teacher_id:
        q = q.where(Visite.teacher_id == teacher_id)
    if date_debut:
        q = q.where(Visite.date_visite >= date_debut)
    if date_fin:
        q = q.where(Visite.date_visite <= date_fin)
    if statut and statut != "toutes":
        q = q.where(Visite.statut == statut)
    return q


@router.get("/visites", response_model=Page[VisiteOut])
async def lister_visites(
    db: DB, _: AdminUser, page: Pagination,
    session_id: Optional[uuid.UUID] = None, school_id: Optional[uuid.UUID] = None,
    superviseur_id: Optional[uuid.UUID] = None, teacher_id: Optional[uuid.UUID] = None,
    date_debut: Optional[date] = None, date_fin: Optional[date] = None,
    statut: str = Query(default="soumise", pattern="^(soumise|annulee|toutes)$"),
) -> Page[VisiteOut]:
    q = _filtres(select(Visite), session_id, school_id, superviseur_id, teacher_id, date_debut, date_fin, statut)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Visite.date_visite.desc(), Visite.created_at.desc())
                             .offset(page.skip).limit(page.limit))).scalars().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=await visite_service.serialiser(db, list(rows)))


@router.get("/visites/export")
async def exporter_visites(
    db: DB, _: AdminUser,
    session_id: Optional[uuid.UUID] = None, school_id: Optional[uuid.UUID] = None,
    superviseur_id: Optional[uuid.UUID] = None, teacher_id: Optional[uuid.UUID] = None,
    date_debut: Optional[date] = None, date_fin: Optional[date] = None,
    statut: str = Query(default="toutes", pattern="^(soumise|annulee|toutes)$"),
) -> StreamingResponse:
    """Une ligne par réponse (visite × item), clés jointives, items manquants inclus."""
    q = _filtres(select(Visite), session_id, school_id, superviseur_id, teacher_id, date_debut, date_fin, statut)
    visites = (await db.execute(q.order_by(Visite.date_visite))).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["visite_id", "date_visite", "statut", "session_id", "ecole_id", "ecole", "classe",
                "superviseur_id", "superviseur", "tuteur_id", "tuteur", "grille_id", "grille_version",
                "duree_minutes", "item_code", "domaine", "type", "bareme_max", "valeur", "disponible",
                "non_applicable", "manquant", "score_obtenu", "score_max"])
    if visites:
        noms = dict((await db.execute(select(User.id, User.name).where(
            User.id.in_({i for v in visites for i in (v.superviseur_id, v.teacher_id)})))).all())
        ecoles = dict((await db.execute(select(School.id, School.name))).all())
        grilles = {g.id: g for g in (await db.execute(select(GrilleVisite))).scalars()}
        items = {}
        for i in (await db.execute(select(GrilleVisiteItem))).scalars():
            items.setdefault(i.grille_id, []).append(i)
        reps = {}
        for r in (await db.execute(select(VisiteReponse).where(VisiteReponse.visite_id.in_([v.id for v in visites])))).scalars():
            reps[(r.visite_id, r.item_id)] = r
        for v in visites:
            for it in sorted(items.get(v.grille_id, []), key=lambda i: (i.ordre, i.code)):
                r = reps.get((v.id, it.id))
                w.writerow([v.id, v.date_visite.isoformat(), v.statut, v.session_id or "", v.school_id or "",
                            safe_cell(ecoles.get(v.school_id, "")), safe_cell(v.classe or ""),
                            v.superviseur_id, safe_cell(noms.get(v.superviseur_id, "")),
                            v.teacher_id, safe_cell(noms.get(v.teacher_id, "")), v.grille_id,
                            grilles[v.grille_id].version, v.duree_minutes or "", it.code, it.domaine, it.type,
                            it.bareme_max or "", "" if not r or r.valeur is None else r.valeur,
                            "" if not r or r.disponible is None else int(r.disponible),
                            int(bool(r and r.non_applicable)), int(r is None), v.score_obtenu or "", v.score_max or ""])
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="visites_{date.today():%Y%m%d}.csv"'},
    )


@router.get("/visites/{visite_id}", response_model=VisiteOut)
async def lire_visite(visite_id: uuid.UUID, db: DB, _: AdminUser) -> VisiteOut:
    v = (await db.execute(select(Visite).where(Visite.id == visite_id))).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "Visite introuvable.")
    return (await visite_service.serialiser(db, [v], detail=True))[0]


@router.post("/visites/{visite_id}/annuler", response_model=VisiteOut)
async def annuler_visite(visite_id: uuid.UUID, body: MotifIn, db: DB, user: AdminUser) -> VisiteOut:
    v = (await db.execute(select(Visite).where(Visite.id == visite_id).with_for_update())).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "Visite introuvable.")
    await visite_service.annuler(db, v, user, body.motif)
    return (await visite_service.serialiser(db, [v], detail=True))[0]
