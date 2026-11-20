"""Endpoints Admin — Rapports Journaliers.

Les photos de classe sont stockées en base sous forme de **data URI base64**
(`photos_classe_url`, liste JSON de 3 au plus). Un rapport pèse couramment 1 à
3 Mo. Aucune route de liste ne doit donc renvoyer ces colonnes : elles sont
servies image par image, en binaire décodé, par
`GET /{rapport_id}/photo/{index}` — deux fois plus léger que le base64, et mis
en cache par le navigateur.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Select, func, select, or_
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.core.export_utils import build_csv_response
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RAPPORT_COMPTE, STATUTS_RAPPORT, RapportJournalier
from app.models.user import User, UserRole
from app.services.rapport_photos import LIST_COLUMNS, NB_PHOTOS, photo_response
from app.schemas.rapport_journalier import RapportAnnulation, RapportJournalierAdminListItem, RapportVersionItem
from app.services import rapport_journalier_service as rj_service

router = APIRouter(prefix="/rapports/journalier", tags=["Admin — Rapports Journaliers"])


# ── Filtres partagés ──────────────────────────────────────────────────────────

def _apply_filters(
    q: Select,
    *,
    teacher_id: Optional[uuid.UUID] = None,
    role:       Optional[UserRole]  = None,
    search:     Optional[str]       = None,
    date_from:  Optional[date]      = None,
    date_to:    Optional[date]      = None,
    ief:        Optional[str]       = None,
    statut:     Optional[str]       = None,
) -> Select:
    """Filtres communs à la liste, l'export, les statistiques et la galerie —
    pour que les quatre vues décrivent toujours le même sous-ensemble.

    Statut (APP-31) : par défaut, les rapports annulés sont exclus de toutes
    les vues ; `statut=annule` les montre seuls, `statut=tous` sans filtre.
    """
    if statut is None:
        q = q.where(RAPPORT_COMPTE)
    elif statut != "tous":
        if statut not in STATUTS_RAPPORT:
            raise HTTPException(422, f"statut inconnu : {statut} (soumis, corrige, annule ou tous).")
        q = q.where(RapportJournalier.statut == statut)
    if role:
        q = q.join(User, User.id == RapportJournalier.teacher_id).where(User.role == role)
    if teacher_id:
        q = q.where(RapportJournalier.teacher_id == teacher_id)
    if search:
        like = f"%{search}%"
        q = q.where(
            or_(
                RapportJournalier.nom_tuteur.ilike(like),
                RapportJournalier.ecole.ilike(like),
                RapportJournalier.ief.ilike(like),
                RapportJournalier.commune.ilike(like),
            )
        )
    if date_from:
        q = q.where(RapportJournalier.date_rapport >= date_from)
    if date_to:
        q = q.where(RapportJournalier.date_rapport <= date_to)
    if ief:
        q = q.where(RapportJournalier.ief.ilike(f"%{ief}%"))
    return q


@router.get("", response_model=Page[RapportJournalierAdminListItem])
async def list_rapports_journalier(
    db: DB,
    _: AdminUser,
    page: Pagination,
    teacher_id:  Optional[uuid.UUID] = None,
    role:        Optional[UserRole]  = None,   # filtre auteur du rapport : enseignant / superviseur
    search:      Optional[str]       = None,   # recherche nom_tuteur / ecole / ief
    date_from:   Optional[date]      = None,
    date_to:     Optional[date]      = None,
    ief:         Optional[str]       = None,
    statut:      Optional[str]       = None,   # soumis | corrige | annule | tous (défaut : hors annulés)
) -> Page[RapportJournalierAdminListItem]:
    base = _apply_filters(
        select(*LIST_COLUMNS), teacher_id=teacher_id, role=role, search=search,
        date_from=date_from, date_to=date_to, ief=ief, statut=statut,
    ).order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).mappings().all()
    items = [RapportJournalierAdminListItem(**r) for r in rows]
    return Page(total=total, skip=page.skip, limit=page.limit, items=items)


# ── Statistiques ──────────────────────────────────────────────────────────────

class Point(BaseModel):
    label: str
    value: int


class RapportStats(BaseModel):
    total:              int
    tuteurs_actifs:     int
    absences_total:     int
    absences_moyenne:   float
    taux_directeur_venu: float   # 0–100
    taux_besoin_appui:   float
    taux_observations:   float
    taux_offline:        float
    rapports_avec_photos: int
    photos_total:         int
    par_mois:      list[Point]
    par_ief:       list[Point]
    top_ecoles:    list[Point]
    difficultes:   list[Point]
    par_jour:      list[Point]


MOIS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
           "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc"]


@router.get("/stats", response_model=RapportStats)
async def stats_rapports_journalier(
    db: DB,
    _: AdminUser,
    teacher_id:  Optional[uuid.UUID] = None,
    role:        Optional[UserRole]  = None,
    search:      Optional[str]       = None,
    date_from:   Optional[date]      = None,
    date_to:     Optional[date]      = None,
    ief:         Optional[str]       = None,
    statut:      Optional[str]       = None,   # soumis | corrige | annule | tous (défaut : hors annulés)
) -> RapportStats:
    """Agrégats sur le même sous-ensemble que la liste — les filtres de l'écran
    s'appliquent aux graphiques."""
    f = dict(teacher_id=teacher_id, role=role, search=search,
             date_from=date_from, date_to=date_to, ief=ief, statut=statut)

    resume = (await db.execute(_apply_filters(select(
        func.count().label("total"),
        func.count(func.distinct(RapportJournalier.teacher_id)).label("tuteurs"),
        func.coalesce(func.sum(RapportJournalier.nb_absences), 0).label("abs_total"),
        func.coalesce(func.avg(RapportJournalier.nb_absences), 0).label("abs_moy"),
        func.count().filter(RapportJournalier.directeur_venu.is_(True)).label("dir"),
        func.count().filter(RapportJournalier.besoin_appui.is_(True)).label("appui"),
        func.count().filter(RapportJournalier.has_observations.is_(True)).label("obs"),
        func.count().filter(RapportJournalier.soumis_en_offline.is_(True)).label("offline"),
        func.count().filter(NB_PHOTOS > 0).label("avec_photos"),
        func.coalesce(func.sum(NB_PHOTOS), 0).label("photos"),
    ), **f))).mappings().one()

    total = resume["total"] or 0
    pct = lambda n: round(n * 100 / total, 1) if total else 0.0   # noqa: E731

    async def groupe(col, limite: Optional[int] = None) -> list[Point]:
        q = _apply_filters(select(col.label("k"), func.count().label("n")), **f) \
            .group_by(col).order_by(func.count().desc())
        if limite:
            q = q.limit(limite)
        return [Point(label=str(r["k"] or "—"), value=r["n"])
                for r in (await db.execute(q)).mappings().all()]

    # Par mois — trié chronologiquement, pas par volume
    mois_rows = (await db.execute(
        _apply_filters(select(
            func.date_trunc("month", RapportJournalier.date_rapport).label("m"),
            func.count().label("n"),
        ), **f).group_by("m").order_by("m")
    )).mappings().all()
    par_mois = [Point(label=f"{MOIS_FR[r['m'].month - 1]} {str(r['m'].year)[2:]}", value=r["n"])
                for r in mois_rows]

    par_jour = [Point(label=f"J{r['k']}", value=r["value"]) for r in [
        {"k": p.label, "value": p.value}
        for p in sorted(await groupe(RapportJournalier.jour_cours),
                        key=lambda x: int(x.label) if x.label.isdigit() else 99)
    ]]

    # Difficultés : colonne texte contenant une liste JSON. L'agrégation se fait
    # en Python — seule cette colonne est rapatriée, jamais les photos.
    diff_rows = (await db.execute(
        _apply_filters(select(RapportJournalier.difficultes), **f)
    )).scalars().all()
    compteur: Counter = Counter()
    for brut in diff_rows:
        if not brut:
            continue
        try:
            parsed = json.loads(brut)
            labels = parsed if isinstance(parsed, list) else [brut]
        except (ValueError, TypeError):
            labels = [brut]
        for lab in labels:
            lab = str(lab).strip()
            if lab:
                compteur[lab] += 1

    return RapportStats(
        total=total,
        tuteurs_actifs=resume["tuteurs"] or 0,
        absences_total=int(resume["abs_total"] or 0),
        absences_moyenne=round(float(resume["abs_moy"] or 0), 1),
        taux_directeur_venu=pct(resume["dir"] or 0),
        taux_besoin_appui=pct(resume["appui"] or 0),
        taux_observations=pct(resume["obs"] or 0),
        taux_offline=pct(resume["offline"] or 0),
        rapports_avec_photos=resume["avec_photos"] or 0,
        photos_total=int(resume["photos"] or 0),
        par_mois=par_mois,
        par_ief=await groupe(RapportJournalier.ief),
        top_ecoles=await groupe(RapportJournalier.ecole, limite=8),
        difficultes=[Point(label=k, value=v) for k, v in compteur.most_common(10)],
        par_jour=par_jour,
    )


# ── Galerie photos ────────────────────────────────────────────────────────────

class PhotoItem(BaseModel):
    id:           uuid.UUID
    date_rapport: date
    nom_tuteur:   str
    ecole:        str
    ief:          str
    commune:      str
    nb_photos:    int


@router.get("/photos", response_model=Page[PhotoItem])
async def list_photos(
    db: DB,
    _: AdminUser,
    page: Pagination,
    teacher_id:  Optional[uuid.UUID] = None,
    role:        Optional[UserRole]  = None,
    search:      Optional[str]       = None,
    date_from:   Optional[date]      = None,
    date_to:     Optional[date]      = None,
    ief:         Optional[str]       = None,
    statut:      Optional[str]       = None,   # soumis | corrige | annule | tous (défaut : hors annulés)
) -> Page[PhotoItem]:
    """Index des rapports comportant au moins une photo — métadonnées seules.
    Les images se chargent ensuite une par une (voir la route ci-dessous)."""
    base = _apply_filters(
        select(
            RapportJournalier.id, RapportJournalier.date_rapport,
            RapportJournalier.nom_tuteur, RapportJournalier.ecole,
            RapportJournalier.ief, RapportJournalier.commune,
            NB_PHOTOS.label("nb_photos"),
        ),
        teacher_id=teacher_id, role=role, search=search,
        date_from=date_from, date_to=date_to, ief=ief, statut=statut,
    ).where(NB_PHOTOS > 0).order_by(RapportJournalier.date_rapport.desc())

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).mappings().all()
    return Page(total=total, skip=page.skip, limit=page.limit,
                items=[PhotoItem(**r) for r in rows])


@router.get("/{rapport_id}/photo/{index}")
async def get_photo(db: DB, _: AdminUser, rapport_id: uuid.UUID, index: int) -> Response:
    """Sert UNE photo, décodée en binaire (voir app/services/rapport_photos.py)."""
    row = (await db.execute(
        select(RapportJournalier.photos_classe_url, RapportJournalier.photo_classe_url)
        .where(RapportJournalier.id == rapport_id)
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable.")
    return photo_response(row["photos_classe_url"], row["photo_classe_url"], index)


@router.get("/export/csv")
async def export_csv(
    db: DB,
    _: AdminUser,
    teacher_id: Optional[uuid.UUID] = None,
    role:       Optional[UserRole]  = None,
    search:     Optional[str]       = None,
    date_from:  Optional[date]      = None,
    date_to:    Optional[date]      = None,
    ief:        Optional[str]       = None,
    statut:      Optional[str]       = None,   # soumis | corrige | annule | tous (défaut : hors annulés)
    fields:     Optional[str]       = None,
) -> StreamingResponse:
    """Exporte les rapports journaliers filtrés en CSV."""
    q = _apply_filters(
        select(RapportJournalier).options(selectinload(RapportJournalier.teacher)),
        teacher_id=teacher_id, role=role, search=search,
        date_from=date_from, date_to=date_to, ief=ief, statut=statut,
    )

    rapports = (await db.execute(q.order_by(RapportJournalier.date_rapport))).scalars().all()

    columns = [
        ("date_rapport",            "date_rapport"),
        ("tuteur",                  "tuteur"),
        ("ief",                     "ief"),
        ("commune",                 "commune"),
        ("ecole",                   "ecole"),
        ("superviseur",             "superviseur"),
        ("nb_absences",             "nb_absences"),
        ("absents",                 "absents"),
        ("semaine",                 "semaine"),
        ("jour_cours",              "jour_cours"),
        ("difficultes",             "difficultes"),
        ("autres_difficultes",      "autres_difficultes"),
        ("description_difficultes", "description_difficultes"),
        ("directeur_venu",          "directeur_venu"),
        ("besoin_appui",            "besoin_appui"),
        ("domaines_appui",          "domaines_appui"),
        ("has_observations",        "has_observations"),
        ("commentaires",            "commentaires"),
        ("soumis_en_offline",       "soumis_en_offline"),
        ("statut",                  "statut"),
        ("version",                 "version"),
        ("statut_motif",            "statut_motif"),
    ]
    rows = [
        [
            r.date_rapport, r.nom_tuteur, r.ief, r.commune, r.ecole, r.superviseur,
            r.nb_absences, r.absents, r.semaine, r.jour_cours,
            r.difficultes, r.autres_difficultes, r.description_difficultes,
            r.directeur_venu, r.besoin_appui, r.domaines_appui,
            r.has_observations, r.commentaires, r.soumis_en_offline,
            r.statut, r.version, r.statut_motif,
        ]
        for r in rapports
    ]

    return build_csv_response(
        columns=columns,
        rows=rows,
        fields=fields,
        filename="rapports_journalier.csv",
    )


# ── Cycle de vie (APP-31) ─────────────────────────────────────────────────────

@router.post("/{rapport_id}/annuler", response_model=RapportJournalierAdminListItem)
async def annuler_rapport(
    rapport_id: uuid.UUID, body: RapportAnnulation, db: DB, current_user: AdminUser,
) -> RapportJournalierAdminListItem:
    """Annule un rapport avec motif. Il reste consultable (`statut=annule`) et
    son contenu est figé dans l'historique ; il sort des indicateurs."""
    r = await rj_service.charger(db, rapport_id)
    await rj_service.annuler(db, r, body, current_user)
    row = (await db.execute(select(*LIST_COLUMNS).where(RapportJournalier.id == r.id))).mappings().one()
    return RapportJournalierAdminListItem(**row)


@router.get("/{rapport_id}/historique", response_model=list[RapportVersionItem])
async def historique_rapport(rapport_id: uuid.UUID, db: DB, _: AdminUser) -> list[RapportVersionItem]:
    """Versions antérieures d'un rapport (corrections et annulation), de la plus ancienne à la plus récente."""
    return [RapportVersionItem.model_validate(v) for v in await rj_service.historique(db, rapport_id)]
