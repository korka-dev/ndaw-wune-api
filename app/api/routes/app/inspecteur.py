"""App inspecteur (lot L8) : consultation, en lecture seule, des écoles de son IEF.

L'inspecteur voit les écoles de son IEF, leurs tuteurs et superviseurs, et
les rapports journaliers envoyés depuis ces écoles. Aucune route d'écriture :
toute modification reste au tuteur, au superviseur ou à l'admin. Un rapport
ou une école hors périmètre répond 404, comme s'il n'existait pas.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import DB, InspecteurUser
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RAPPORT_COMPTE, RapportJournalier
from app.models.school import School
from app.models.user import User, UserRole, UserStatus
from app.schemas.rapport_journalier import RapportJournalierListItem
from app.services.archivage import non_archive
from app.services.inspecteur_service import ecole_ids, filtre_rapports
from app.services.rapport_photos import LIST_COLUMNS, photo_response

router = APIRouter(prefix="/inspecteur", tags=["App — Inspecteur (consultation)"])


class ResumeIEF(BaseModel):
    ief: Optional[str]
    nb_ecoles: int
    nb_tuteurs: int
    nb_superviseurs: int
    rapports_7_jours: int
    besoins_appui_7_jours: int
    dernier_rapport: Optional[date] = None


class EcoleIEF(BaseModel):
    id: uuid.UUID
    nom: str
    commune: Optional[str] = None
    code_ecole: Optional[int] = None
    directeur: Optional[str] = None
    nb_tuteurs: int = 0
    nb_superviseurs: int = 0
    rapports_7_jours: int = 0
    besoins_appui_7_jours: int = 0
    dernier_rapport: Optional[date] = None


class PersonneEcole(BaseModel):
    id: uuid.UUID
    nom: str
    role: str
    telephone: Optional[str] = None
    classes: list[str] = []
    dernier_rapport: Optional[date] = None
    rapports_7_jours: int = 0


def _il_y_a_7() -> date:
    return date.today() - timedelta(days=7)


async def _comptes_par_ecole(db, ids: set[uuid.UUID], role: UserRole) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    return dict((await db.execute(
        select(User.school_id, func.count())
        .where(User.school_id.in_(ids), User.role == role, User.status == UserStatus.actif,
               non_archive(User, False))
        .group_by(User.school_id)
    )).all())


@router.get("/resume", response_model=ResumeIEF)
async def resume(current_user: InspecteurUser, db: DB) -> ResumeIEF:
    ids = await ecole_ids(db, current_user, archives=False)
    tuteurs = await _comptes_par_ecole(db, ids, UserRole.enseignant)
    sups = await _comptes_par_ecole(db, ids, UserRole.superviseur)
    perimetre = filtre_rapports(await ecole_ids(db, current_user), current_user)
    recents, appui, dernier = (await db.execute(
        select(
            func.count().filter(RapportJournalier.date_rapport >= _il_y_a_7()),
            func.count().filter(RapportJournalier.date_rapport >= _il_y_a_7(), RapportJournalier.besoin_appui.is_(True)),
            func.max(RapportJournalier.date_rapport),
        ).where(perimetre, RAPPORT_COMPTE)
    )).one()
    return ResumeIEF(
        ief=current_user.ief, nb_ecoles=len(ids), nb_tuteurs=sum(tuteurs.values()),
        nb_superviseurs=sum(sups.values()), rapports_7_jours=int(recents or 0),
        besoins_appui_7_jours=int(appui or 0), dernier_rapport=dernier,
    )


@router.get("/ecoles", response_model=list[EcoleIEF])
async def ecoles(current_user: InspecteurUser, db: DB) -> list[EcoleIEF]:
    """Écoles actives de l'IEF, avec l'activité des 7 derniers jours."""
    ids = await ecole_ids(db, current_user, archives=False)
    if not ids:
        return []
    rows = (await db.execute(select(School).where(School.id.in_(ids)).order_by(School.name))).scalars().all()
    tuteurs = await _comptes_par_ecole(db, ids, UserRole.enseignant)
    sups = await _comptes_par_ecole(db, ids, UserRole.superviseur)
    stats = {
        r.ecole_id: r for r in (await db.execute(
            select(
                RapportJournalier.ecole_id,
                func.count().filter(RapportJournalier.date_rapport >= _il_y_a_7()).label("recents"),
                func.count().filter(RapportJournalier.date_rapport >= _il_y_a_7(),
                                    RapportJournalier.besoin_appui.is_(True)).label("appui"),
                func.max(RapportJournalier.date_rapport).label("dernier"),
            ).where(RapportJournalier.ecole_id.in_(ids), RAPPORT_COMPTE)
            .group_by(RapportJournalier.ecole_id)
        )).all()
    }
    out = []
    for s in rows:
        st = stats.get(s.id)
        out.append(EcoleIEF(
            id=s.id, nom=s.name, commune=s.city, code_ecole=s.code_ecole, directeur=s.director,
            nb_tuteurs=tuteurs.get(s.id, 0), nb_superviseurs=sups.get(s.id, 0),
            rapports_7_jours=int(st.recents) if st else 0, besoins_appui_7_jours=int(st.appui) if st else 0,
            dernier_rapport=st.dernier if st else None,
        ))
    return out


@router.get("/ecoles/{ecole_id}/personnes", response_model=list[PersonneEcole])
async def personnes_ecole(ecole_id: uuid.UUID, current_user: InspecteurUser, db: DB) -> list[PersonneEcole]:
    """Tuteurs et superviseurs actifs rattachés à une école de l'IEF."""
    if ecole_id not in await ecole_ids(db, current_user):
        raise HTTPException(status_code=404, detail="École introuvable dans votre IEF.")
    gens = (await db.execute(
        select(User).where(
            User.school_id == ecole_id, User.role.in_([UserRole.enseignant, UserRole.superviseur]),
            User.status == UserStatus.actif, non_archive(User, False),
        ).order_by(User.role, User.name)
    )).scalars().all()
    if not gens:
        return []
    ids = [u.id for u in gens]
    derniers = dict((await db.execute(
        select(RapportJournalier.teacher_id, func.max(RapportJournalier.date_rapport))
        .where(RapportJournalier.teacher_id.in_(ids), RAPPORT_COMPTE).group_by(RapportJournalier.teacher_id)
    )).all())
    recents = dict((await db.execute(
        select(RapportJournalier.teacher_id, func.count())
        .where(RapportJournalier.teacher_id.in_(ids), RAPPORT_COMPTE, RapportJournalier.date_rapport >= _il_y_a_7())
        .group_by(RapportJournalier.teacher_id)
    )).all())
    return [
        PersonneEcole(
            id=u.id, nom=u.name, role="tuteur" if u.role == UserRole.enseignant else "superviseur",
            telephone=u.phone,
            # Pour un superviseur, `classes` contient les identifiants des tuteurs suivis : non affiché.
            classes=list(u.classes or []) if u.role == UserRole.enseignant else [],
            dernier_rapport=derniers.get(u.id), rapports_7_jours=int(recents.get(u.id, 0)),
        )
        for u in gens
    ]


@router.get("/rapports", response_model=Page[RapportJournalierListItem])
async def rapports(
    current_user: InspecteurUser, db: DB, page: Pagination,
    ecole_id: Optional[uuid.UUID] = None,
    auteur_id: Optional[uuid.UUID] = Query(None, description="Tuteur ou superviseur auteur du rapport"),
    date_debut: Optional[date] = None, date_fin: Optional[date] = None,
    besoin_appui: Optional[bool] = None,
    inclure_annules: bool = False,
) -> Page[RapportJournalierListItem]:
    """Rapports journaliers des écoles de l'IEF (sans photos), du plus récent au plus ancien."""
    ids = await ecole_ids(db, current_user)
    if ecole_id is not None:
        if ecole_id not in ids:
            raise HTTPException(status_code=404, detail="École introuvable dans votre IEF.")
        condition = RapportJournalier.ecole_id == ecole_id
    else:
        condition = filtre_rapports(ids, current_user)
    stmt = select(*LIST_COLUMNS).where(condition)
    if auteur_id is not None:
        stmt = stmt.where(RapportJournalier.teacher_id == auteur_id)
    if date_debut:
        stmt = stmt.where(RapportJournalier.date_rapport >= date_debut)
    if date_fin:
        stmt = stmt.where(RapportJournalier.date_rapport <= date_fin)
    if besoin_appui is not None:
        stmt = stmt.where(RapportJournalier.besoin_appui.is_(besoin_appui))
    if not inclure_annules:
        stmt = stmt.where(RAPPORT_COMPTE)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(
        stmt.order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
        .offset(page.skip).limit(page.limit)
    )).mappings().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=[RapportJournalierListItem(**r) for r in rows])


async def _rapport_du_perimetre(db, current_user: User, rapport_id: uuid.UUID, *colonnes):
    row = (await db.execute(
        select(*colonnes).where(
            RapportJournalier.id == rapport_id,
            filtre_rapports(await ecole_ids(db, current_user), current_user),
        )
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable dans votre IEF.")
    return row


@router.get("/rapports/{rapport_id}", response_model=RapportJournalierListItem)
async def rapport(rapport_id: uuid.UUID, current_user: InspecteurUser, db: DB) -> RapportJournalierListItem:
    return RapportJournalierListItem(**await _rapport_du_perimetre(db, current_user, rapport_id, *LIST_COLUMNS))


@router.get("/rapports/{rapport_id}/photo/{index}")
async def photo(rapport_id: uuid.UUID, index: int, current_user: InspecteurUser, db: DB) -> Response:
    row = await _rapport_du_perimetre(
        db, current_user, rapport_id, RapportJournalier.photos_classe_url, RapportJournalier.photo_classe_url,
    )
    return photo_response(row["photos_classe_url"], row["photo_classe_url"], index)
