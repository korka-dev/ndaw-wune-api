"""Endpoints App mobile — Rapports Journaliers (soumission par le tuteur/superviseur).

Accessible aux enseignants ET aux superviseurs (MobileUser).

Rapport V2 :
- APP-16 : idempotence par clé d'opération client (`client_op_id`). Dix envois
  de la même saisie créent un seul rapport ; la même clé avec un autre contenu
  est refusée (409 IDEMPOTENCY_CONFLICT).
- APP-37 : listes et accusés de réception sans photos ; chaque photo se charge
  à la demande via GET /{id}/photo/{index}.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.deps import DB, MobileUser
from app.core.pagination import Page, Pagination
from app.models.rapport_journalier import RapportJournalier
from app.schemas.rapport_journalier import RapportJournalierCreate, RapportJournalierListItem
from app.services.rapport_photos import LIST_COLUMNS, photo_response, photos_de

router = APIRouter(prefix="/rapports/journalier", tags=["App — Rapports Journaliers"])


def content_hash(data: dict) -> str:
    """Empreinte stable du contenu soumis (hors clé d'opération)."""
    canon = json.dumps(
        {k: v for k, v in data.items() if k != "client_op_id"},
        sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _light(rapport: RapportJournalier) -> RapportJournalierListItem:
    data ={f: getattr(rapport, f) for f in RapportJournalierListItem.model_fields if f != "nb_photos"}
    data["nb_photos"] = len(photos_de(rapport.photos_classe_url, rapport.photo_classe_url))
    return RapportJournalierListItem(**data)


async def _by_client_op(db, teacher_id: uuid.UUID, op_id: str) -> RapportJournalier | None:
    return (await db.execute(
        select(RapportJournalier).where(
            RapportJournalier.teacher_id == teacher_id,
            RapportJournalier.client_op_id == op_id,
        )
    )).scalar_one_or_none()


def _replay_or_conflict(existing: RapportJournalier, digest: str) -> RapportJournalierListItem:
    if existing.client_op_hash and existing.client_op_hash != digest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDEMPOTENCY_CONFLICT",
                "message": "Cette clé d'envoi a déjà servi pour un rapport au contenu différent.",
                "rapport_id": str(existing.id),
            },
        )
    return _light(existing)


@router.post("", response_model=RapportJournalierListItem, status_code=status.HTTP_201_CREATED)
async def submit_rapport_journalier(
    body: RapportJournalierCreate,
    current_user: MobileUser,
    db: DB,
) -> RapportJournalierListItem:
    """Soumet un rapport journalier depuis l'app mobile (enseignant ou superviseur, online/offline)."""
    data = body.model_dump()
    op_id = data.pop("client_op_id", None)
    digest = content_hash(data)

    if op_id:
        existing = await _by_client_op(db, current_user.id, op_id)
        if existing is not None:
            return _replay_or_conflict(existing, digest)
    else:
        # Anciennes versions de l'app (sans clé) : un rapport strictement
        # identique du même auteur est renvoyé tel quel au lieu d'être dupliqué.
        doublon = (await db.execute(
            select(RapportJournalier).where(
                RapportJournalier.teacher_id == current_user.id,
                RapportJournalier.date_rapport == data["date_rapport"],
                RapportJournalier.semaine == data["semaine"],
                RapportJournalier.jour_cours == data["jour_cours"],
                RapportJournalier.ecole == data["ecole"],
                RapportJournalier.nom_tuteur == data["nom_tuteur"],
                RapportJournalier.nb_absences == data["nb_absences"],
                RapportJournalier.absents.is_not_distinct_from(data["absents"]),
                RapportJournalier.difficultes == data["difficultes"],
                RapportJournalier.commentaires.is_not_distinct_from(data["commentaires"]),
                RapportJournalier.reponses_questions.is_not_distinct_from(data["reponses_questions"]),
            ).limit(1)
        )).scalar_one_or_none()
        if doublon is not None:
            return _light(doublon)

    rapport = RapportJournalier(
        teacher_id=current_user.id, client_op_id=op_id, client_op_hash=digest if op_id else None, **data,
    )
    try:
        # Point de sauvegarde : deux envois simultanés de la même clé → le
        # second échoue sur l'unicité et renvoie le rapport du premier.
        async with db.begin_nested():
            db.add(rapport)
            await db.flush()
    except IntegrityError:
        existing = await _by_client_op(db, current_user.id, op_id) if op_id else None
        if existing is None:
            raise
        return _replay_or_conflict(existing, digest)
    await db.refresh(rapport)
    return _light(rapport)


@router.get("", response_model=Page[RapportJournalierListItem])
async def list_rapports_journalier(
    current_user: MobileUser,
    db: DB,
    page: Pagination,
) -> Page[RapportJournalierListItem]:
    """Historique paginé des rapports journaliers de l'utilisateur connecté (sans photos)."""
    base = (
        select(*LIST_COLUMNS)
        .where(RapportJournalier.teacher_id == current_user.id)
        .order_by(RapportJournalier.date_rapport.desc(), RapportJournalier.created_at.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.offset(page.skip).limit(page.limit))).mappings().all()
    return Page(total=total, skip=page.skip, limit=page.limit, items=[RapportJournalierListItem(**r) for r in rows])


@router.get("/{rapport_id}/photo/{index}")
async def get_photo(rapport_id: uuid.UUID, index: int, current_user: MobileUser, db: DB) -> Response:
    """Une photo d'un rapport de l'utilisateur connecté, décodée en binaire."""
    row = (await db.execute(
        select(RapportJournalier.photos_classe_url, RapportJournalier.photo_classe_url).where(
            RapportJournalier.id == rapport_id,
            RapportJournalier.teacher_id == current_user.id,
        )
    )).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable.")
    return photo_response(row["photos_classe_url"], row["photo_classe_url"], index)


@router.delete("/{rapport_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rapport_journalier(
    rapport_id: uuid.UUID,
    current_user: MobileUser,
    db: DB,
) -> Response:
    """Supprime un rapport journalier appartenant à l'utilisateur connecté."""
    result = await db.execute(
        select(RapportJournalier).where(
            RapportJournalier.id == rapport_id,
            RapportJournalier.teacher_id == current_user.id,
        )
    )
    rapport = result.scalar_one_or_none()
    if rapport is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport introuvable.")
    await db.delete(rapport)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
