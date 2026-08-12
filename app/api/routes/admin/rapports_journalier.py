"""Endpoints Admin — Rapports Journaliers.

Les photos de classe sont stockées en base sous forme de **data URI base64**
(`photos_classe_url`, liste JSON de 3 au plus). Un rapport pèse couramment 1 à
3 Mo. Aucune route de liste ne doit donc renvoyer ces colonnes : elles sont
servies image par image, en binaire décodé, par
`GET /{rapport_id}/photo/{index}` — deux fois plus léger que le base64, et mis
en cache par le navigateur.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import re
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
from app.models.rapport_journalier import RapportJournalier
from app.models.user import User, UserRole
from app.schemas.rapport_journalier import (
    RapportJournalierAdminListItem,
    RapportJournalierResponse,
)

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
) -> Select:
    """Filtres communs à la liste, l'export, les statistiques et la galerie —
    pour que les quatre vues décrivent toujours le même sous-ensemble."""
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


# Nombre de photos calculé DANS Postgres, sans rapatrier le base64 : on compte
# les occurrences du préfixe « data: » dans le texte. Volontairement tolérant —
# un JSON malformé donnerait 0 plutôt que de faire échouer toute la requête,
# ce que ferait un json_array_length().
def _compte_data_uri(col):
    return func.coalesce(
        (func.length(col) - func.length(func.replace(col, "data:", ""))) / 5, 0
    )


# GREATEST des deux colonnes plutôt qu'un repli : un `photos_classe_url` valant
# « [] » compterait 0 et masquerait la photo de l'ancienne colonne.
_NB_PHOTOS = func.greatest(
    _compte_data_uri(RapportJournalier.photos_classe_url),
    _compte_data_uri(RapportJournalier.photo_classe_url),
)

_LIST_COLUMNS = [
    RapportJournalier.id, RapportJournalier.teacher_id, RapportJournalier.date_rapport,
    RapportJournalier.ief, RapportJournalier.commune, RapportJournalier.ecole,
    RapportJournalier.superviseur, RapportJournalier.nom_tuteur,
    RapportJournalier.nb_absences, RapportJournalier.absents,
    RapportJournalier.semaine, RapportJournalier.jour_cours,
    RapportJournalier.difficultes, RapportJournalier.autres_difficultes,
    RapportJournalier.description_difficultes, RapportJournalier.directeur_venu,
    RapportJournalier.besoin_appui, RapportJournalier.domaines_appui,
    RapportJournalier.has_observations, RapportJournalier.commentaires,
    RapportJournalier.soumis_en_offline, RapportJournalier.reponses_questions,
    RapportJournalier.created_at,
    _NB_PHOTOS.label("nb_photos"),
]


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
) -> Page[RapportJournalierAdminListItem]:
    base = _apply_filters(
        select(*_LIST_COLUMNS), teacher_id=teacher_id, role=role, search=search,
        date_from=date_from, date_to=date_to, ief=ief,
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
) -> RapportStats:
    """Agrégats sur le même sous-ensemble que la liste — les filtres de l'écran
    s'appliquent aux graphiques."""
    f = dict(teacher_id=teacher_id, role=role, search=search,
             date_from=date_from, date_to=date_to, ief=ief)

    resume = (await db.execute(_apply_filters(select(
        func.count().label("total"),
        func.count(func.distinct(RapportJournalier.teacher_id)).label("tuteurs"),
        func.coalesce(func.sum(RapportJournalier.nb_absences), 0).label("abs_total"),
        func.coalesce(func.avg(RapportJournalier.nb_absences), 0).label("abs_moy"),
        func.count().filter(RapportJournalier.directeur_venu.is_(True)).label("dir"),
        func.count().filter(RapportJournalier.besoin_appui.is_(True)).label("appui"),
        func.count().filter(RapportJournalier.has_observations.is_(True)).label("obs"),
        func.count().filter(RapportJournalier.soumis_en_offline.is_(True)).label("offline"),
        func.count().filter(_NB_PHOTOS > 0).label("avec_photos"),
        func.coalesce(func.sum(_NB_PHOTOS), 0).label("photos"),
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
) -> Page[PhotoItem]:
    """Index des rapports comportant au moins une photo — métadonnées seules.
    Les images se chargent ensuite une par une (voir la route ci-dessous)."""
    base = _apply_filters(
        select(
            RapportJournalier.id, RapportJournalier.date_rapport,
            RapportJournalier.nom_tuteur, RapportJournalier.ecole,
            RapportJournalier.ief, RapportJournalier.commune,
            _NB_PHOTOS.label("nb_photos"),
        ),
        teacher_id=teacher_id, role=role, search=search,
        date_from=date_from, date_to=date_to, ief=ief,
    ).where(_NB_PHOTOS > 0).order_by(RapportJournalier.date_rapport.desc())

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).mappings().all()
    return Page(total=total, skip=page.skip, limit=page.limit,
                items=[PhotoItem(**r) for r in rows])


def _photos_de(photos_json: Optional[str], photo_unique: Optional[str]) -> list[str]:
    """Les deux colonnes coexistent : `photos_classe_url` (liste JSON, jusqu'à
    3 photos) a remplacé `photo_classe_url` (une seule), gardée pour les
    rapports anciens."""
    if photos_json:
        try:
            parsed = json.loads(photos_json)
            if isinstance(parsed, list):
                urls = [u for u in parsed if isinstance(u, str) and u]
                if urls:
                    return urls
        except (ValueError, TypeError):
            pass
    return [photo_unique] if photo_unique else []


_DATA_URI = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?;base64,(.+)$", re.DOTALL)


@router.get("/{rapport_id}/photo/{index}")
async def get_photo(db: DB, _: AdminUser, rapport_id: uuid.UUID, index: int) -> Response:
    """Sert UNE photo, décodée en binaire.

    Le stockage est un data URI base64 : le renvoyer tel quel coûterait 33 % de
    plus et ne serait pas mis en cache. On décode, on annonce le vrai type MIME,
    et on autorise le cache privé — l'image d'un rapport ne change jamais.
    """
    row = (await db.execute(
        select(RapportJournalier.photos_classe_url, RapportJournalier.photo_classe_url)
        .where(RapportJournalier.id == rapport_id)
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable.")

    urls = _photos_de(row["photos_classe_url"], row["photo_classe_url"])
    if index < 0 or index >= len(urls):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo introuvable.")

    m = _DATA_URI.match(urls[index].strip())
    if not m:
        # Ancien format : une vraie URL. On la renvoie au client, à lui d'aller
        # la chercher — le serveur ne relaie pas de ressource externe.
        return Response(content=urls[index], media_type="text/plain")
    try:
        binaire = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Photo illisible.")

    return Response(
        content=binaire,
        media_type=_type_reel(binaire, m.group(1)),
        headers={"Cache-Control": "private, max-age=86400"},
    )


def _type_reel(binaire: bytes, declare: Optional[str]) -> str:
    """Type MIME déduit des octets, pas de l'étiquette du data URI.

    L'app mobile annonce systématiquement `image/jpeg`, y compris pour des PNG
    (constaté en base : un data URI « data:image/jpeg;base64,iVBORw0KGgo… », or
    iVBORw est la signature PNG). Les navigateurs corrigent d'eux-mêmes, mais un
    téléchargement ou un outil tiers se fierait à l'en-tête.
    """
    if binaire.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if binaire.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if binaire.startswith(b"GIF8"):
        return "image/gif"
    if binaire[:4] == b"RIFF" and binaire[8:12] == b"WEBP":
        return "image/webp"
    return declare or "image/jpeg"


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
    fields:     Optional[str]       = None,
) -> StreamingResponse:
    """Exporte les rapports journaliers filtrés en CSV."""
    q = select(RapportJournalier).options(selectinload(RapportJournalier.teacher))
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
            )
        )
    if date_from:
        q = q.where(RapportJournalier.date_rapport >= date_from)
    if date_to:
        q = q.where(RapportJournalier.date_rapport <= date_to)
    if ief:
        q = q.where(RapportJournalier.ief.ilike(f"%{ief}%"))

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
    ]
    rows = [
        [
            r.date_rapport, r.nom_tuteur, r.ief, r.commune, r.ecole, r.superviseur,
            r.nb_absences, r.absents, r.semaine, r.jour_cours,
            r.difficultes, r.autres_difficultes, r.description_difficultes,
            r.directeur_venu, r.besoin_appui, r.domaines_appui,
            r.has_observations, r.commentaires, r.soumis_en_offline,
        ]
        for r in rapports
    ]

    return build_csv_response(
        columns=columns,
        rows=rows,
        fields=fields,
        filename="rapports_journalier.csv",
    )
