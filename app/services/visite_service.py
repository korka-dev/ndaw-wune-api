"""Visites pédagogiques (lot L6, D2.3 / D9.3).

- La grille utilisée doit être publiée (`active`) et valable pour la campagne.
- Le tuteur observé doit relever du superviseur (même périmètre que /sync).
- Les réponses sont contrôlées contre la grille : item inconnu, valeur hors
  barème, réponse en double ou item obligatoire manquant → 422 détaillé.
- Les scores sont recalculés par le serveur à partir des seuls items
  applicables et renseignés ; « non applicable » et « manquant » ne valent
  jamais zéro (Annexe A : barème, manquants et non applicables conservés).
- Idempotence (APP-16) : `client_op_id` par superviseur, 409 si la même clé
  arrive avec un contenu différent.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demande_appui import DemandeAppui
from app.models.school import School
from app.models.user import User
from app.models.visite import GrilleVisite, GrilleVisiteItem, Visite, VisiteReponse
from app.schemas.visite import GrilleItemOut, GrilleOut, ReponseOut, VisiteIn, VisiteOut
from app.services import demande_appui_service
from app.services.audit_service import journaliser
from app.services.progression_service import active_session_for
from app.services.supervisor_service import is_supervised_teacher


# ── Grilles ───────────────────────────────────────────────────────────────────

async def items_de(db: AsyncSession, grille_id: uuid.UUID) -> list[GrilleVisiteItem]:
    return list((await db.execute(
        select(GrilleVisiteItem).where(GrilleVisiteItem.grille_id == grille_id)
        .order_by(GrilleVisiteItem.ordre, GrilleVisiteItem.code)
    )).scalars().all())


async def grille_out(db: AsyncSession, g: GrilleVisite) -> GrilleOut:
    o = GrilleOut.model_validate(g)
    o.items = [GrilleItemOut.model_validate(i) for i in await items_de(db, g.id)]
    o.nb_visites = (await db.execute(select(func.count()).select_from(Visite).where(Visite.grille_id == g.id))).scalar_one()
    return o


async def grilles_utilisables(db: AsyncSession, session_id: Optional[uuid.UUID]) -> list[GrilleVisite]:
    cond = GrilleVisite.session_id.is_(None)
    if session_id:
        cond = or_(cond, GrilleVisite.session_id == session_id)
    return list((await db.execute(
        select(GrilleVisite).where(GrilleVisite.statut == "active", cond)
        .order_by(GrilleVisite.nom, GrilleVisite.version.desc())
    )).scalars().all())


# ── Scores ────────────────────────────────────────────────────────────────────

def calculer(items: list[GrilleVisiteItem], reponses: dict[uuid.UUID, VisiteReponse]) -> dict:
    """Score par domaine : somme des valeurs / somme des barèmes des items
    applicables ET renseignés. Les manquants sont comptés à part."""
    domaines: dict[str, dict] = {}
    total = maxi = 0.0
    manquants = outils_ko = 0
    for it in items:
        r = reponses.get(it.id)
        if it.type == "outil":
            if r is not None and not r.non_applicable and r.disponible is False:
                outils_ko += 1
            if r is None or (r.disponible is None and not r.non_applicable):
                manquants += 1
            continue
        if it.type == "texte":
            continue
        if r is not None and r.non_applicable:
            continue
        if r is None or r.valeur is None:
            manquants += 1
            continue
        d = domaines.setdefault(it.domaine, {"obtenu": 0.0, "max": 0.0, "items": 0})
        d["obtenu"] += r.valeur
        d["max"] += float(it.bareme_max or 0)
        d["items"] += 1
        total += r.valeur
        maxi += float(it.bareme_max or 0)
    for d in domaines.values():
        d["taux"] = round(100 * d["obtenu"] / d["max"], 1) if d["max"] else None
    return {
        "score_obtenu": total if maxi else None, "score_max": maxi or None,
        "scores_domaines": domaines, "nb_items_manquants": manquants, "nb_outils_indisponibles": outils_ko,
    }


def _valider(items: list[GrilleVisiteItem], body: VisiteIn) -> list[VisiteReponse]:
    par_id = {i.id: i for i in items}
    erreurs, vues, lignes = [], set(), []
    for idx, r in enumerate(body.reponses):
        it = par_id.get(r.item_id)
        loc = ["body", "reponses", idx]
        if it is None:
            erreurs.append({"loc": loc, "msg": "item absent de la grille."})
            continue
        if r.item_id in vues:
            erreurs.append({"loc": loc, "msg": f"{it.code} : réponse en double."})
            continue
        vues.add(r.item_id)
        if not r.non_applicable:
            if it.type == "echelle" and r.valeur is not None and not (0 <= r.valeur <= (it.bareme_max or 0)):
                erreurs.append({"loc": loc, "msg": f"{it.code} : valeur hors barème (0 à {it.bareme_max})."})
            if it.type == "oui_non" and r.valeur is not None and r.valeur not in (0, 1):
                erreurs.append({"loc": loc, "msg": f"{it.code} : 0 ou 1 attendu."})
            if it.type in ("echelle", "oui_non") and r.disponible is not None:
                erreurs.append({"loc": loc, "msg": f"{it.code} : « disponible » réservé aux outils."})
            if it.type == "outil" and r.valeur is not None:
                erreurs.append({"loc": loc, "msg": f"{it.code} : un outil se renseigne par « disponible »."})
        lignes.append(VisiteReponse(
            item_id=r.item_id, valeur=None if r.non_applicable else r.valeur,
            disponible=None if r.non_applicable else r.disponible,
            texte=r.texte, non_applicable=r.non_applicable,
        ))
    for it in items:
        if it.obligatoire and it.id not in vues:
            erreurs.append({"loc": ["body", "reponses"], "msg": f"{it.code} : item obligatoire non renseigné."})
    if erreurs:
        raise HTTPException(422, {"code": "VISITE_INVALIDE", "erreurs": erreurs})
    return lignes


def _empreinte(body: VisiteIn) -> str:
    contenu = body.model_dump(mode="json", exclude={"client_op_id", "submitted_at"})
    return hashlib.sha256(json.dumps(contenu, sort_keys=True).encode()).hexdigest()


# ── Création ──────────────────────────────────────────────────────────────────

async def creer(db: AsyncSession, superviseur: User, body: VisiteIn) -> Visite:
    digest = _empreinte(body)
    if body.client_op_id:
        existante = (await db.execute(select(Visite).where(
            Visite.superviseur_id == superviseur.id, Visite.client_op_id == body.client_op_id,
        ))).scalar_one_or_none()
        if existante is not None:
            if existante.client_op_hash != digest:
                raise HTTPException(409, {"code": "CLIENT_OP_CONFLIT",
                                          "message": "Cette opération a déjà été reçue avec un contenu différent."})
            return existante

    if not await is_supervised_teacher(db, superviseur, body.teacher_id):
        raise HTTPException(403, {"code": "HORS_PERIMETRE", "message": "Ce tuteur ne relève pas de votre supervision."})
    tuteur = (await db.execute(select(User).where(User.id == body.teacher_id))).scalar_one()
    session = await active_session_for(db, tuteur)
    grille = (await db.execute(select(GrilleVisite).where(GrilleVisite.id == body.grille_id))).scalar_one_or_none()
    if grille is None or grille.statut != "active":
        raise HTTPException(422, {"code": "GRILLE_INDISPONIBLE", "message": "Grille inconnue ou non publiée."})
    if grille.session_id and (session is None or grille.session_id != session.id):
        raise HTTPException(422, {"code": "GRILLE_HORS_CAMPAGNE", "message": "Cette grille ne s'applique pas à la campagne du tuteur."})
    if body.classe and tuteur.classes and body.classe.strip().lower() not in {c.strip().lower() for c in tuteur.classes}:
        raise HTTPException(422, {"code": "CLASSE_HORS_AFFECTATION", "message": "Classe non affectée à ce tuteur."})

    items = await items_de(db, grille.id)
    reponses = _valider(items, body)
    scores = calculer(items, {r.item_id: r for r in reponses})

    v = Visite(
        superviseur_id=superviseur.id, teacher_id=tuteur.id, school_id=tuteur.school_id,
        classe=body.classe or (tuteur.classes[0] if tuteur.classes and len(tuteur.classes) == 1 else None),
        session_id=session.id if session else None, grille_id=grille.id, date_visite=body.date_visite,
        heure_debut=body.heure_debut, duree_minutes=body.duree_minutes, points_appui=body.points_appui,
        points_amelioration=body.points_amelioration, commentaire=body.commentaire,
        client_op_id=body.client_op_id, client_op_hash=digest if body.client_op_id else None,
        submitted_at=body.submitted_at, **scores,
    )
    try:
        async with db.begin_nested():
            db.add(v)
            await db.flush()
    except IntegrityError:
        existante = (await db.execute(select(Visite).where(
            Visite.superviseur_id == superviseur.id, Visite.client_op_id == body.client_op_id,
        ))).scalar_one_or_none()
        if existante is None:
            raise
        return existante
    for r in reponses:
        r.visite_id = v.id
        db.add(r)
    for rec in body.recommandations:
        await demande_appui_service.creer(
            db, superviseur, origine="visite", titre=rec.titre, description=rec.description,
            domaine=rec.domaine, beneficiaire_id=tuteur.id, school_id=tuteur.school_id,
            session_id=v.session_id, responsable_id=superviseur.id, echeance=rec.echeance, visite_id=v.id,
        )
    await db.flush()
    return v


async def annuler(db: AsyncSession, v: Visite, acteur: User, motif: str) -> Visite:
    if v.statut == "annulee":
        return v
    v.statut = "annulee"
    v.statut_motif = motif
    v.statut_par_id = acteur.id
    v.statut_at = datetime.now(timezone.utc)
    journaliser(db, acteur=acteur, action="annulation", entity="Visites pédagogiques",
                description=f"Annulation de la visite du {v.date_visite:%d/%m/%Y}",
                cible_type="visite", cible_id=v.id, motif=motif, path=f"/visites/{v.id}/annuler")
    await db.flush()
    return v


async def serialiser(db: AsyncSession, visites: list[Visite], detail: bool = False) -> list[VisiteOut]:
    if not visites:
        return []
    ids_u = {i for v in visites for i in (v.superviseur_id, v.teacher_id)}
    noms = dict((await db.execute(select(User.id, User.name).where(User.id.in_(ids_u)))).all())
    ids_s = {v.school_id for v in visites if v.school_id}
    ecoles = dict((await db.execute(select(School.id, School.name).where(School.id.in_(ids_s)))).all()) if ids_s else {}
    versions = dict((await db.execute(select(GrilleVisite.id, GrilleVisite.version).where(
        GrilleVisite.id.in_({v.grille_id for v in visites})))).all())
    out = []
    for v in visites:
        o = VisiteOut.model_validate(v)
        o.superviseur_nom = noms.get(v.superviseur_id)
        o.teacher_nom = noms.get(v.teacher_id)
        o.ecole = ecoles.get(v.school_id)
        o.grille_version = versions.get(v.grille_id)
        if detail:
            o.reponses = [ReponseOut.model_validate(r) for r in (await db.execute(
                select(VisiteReponse).where(VisiteReponse.visite_id == v.id))).scalars()]
            demandes = list((await db.execute(select(DemandeAppui).where(DemandeAppui.visite_id == v.id))).scalars())
            o.demandes = await demande_appui_service.serialiser(db, demandes)
        out.append(o)
    return out
