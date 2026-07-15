"""
Endpoints Admin — Sujets d'évaluation.
L'admin crée un sujet → le système tire aléatoirement des élèves.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DB
from app.models.eleve import Eleve
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.session import ProgramSession, SessionStatus
from app.models.user import User, UserRole

router = APIRouter(prefix="/evaluation-sujets", tags=["Admin — Evaluation Sujets"])


# ── Schémas ────────────────────────────────────────────────────────────────────

class SujetCreate(BaseModel):
    titre: str
    description: Optional[str] = None
    nb_eleves_par_classe: int = 5    # 0 = tous les élèves


class TirageOut(BaseModel):
    id: str
    eleve_id: str
    eleve_nom: str
    eleve_prenom: Optional[str] = None
    eleve_classe: Optional[str] = None
    eleve_school: Optional[str] = None
    superviseur_id: Optional[str] = None
    superviseur_nom: Optional[str] = None
    resultat: Optional[str] = None
    commentaire: Optional[str] = None
    date_eval: Optional[str] = None
    audio_filename: Optional[str] = None
    created_at: str


class SujetOut(BaseModel):
    id: str
    titre: str
    description: Optional[str] = None
    nb_eleves_par_classe: int
    session_id: Optional[str] = None
    nb_tirages: int = 0
    nb_evalues: int = 0
    created_at: str


class SujetDetail(SujetOut):
    tirages: list[TirageOut] = []


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _random_eleves(db, nb_par_classe: int) -> list[uuid.UUID]:
    """Tire aléatoirement nb_par_classe élèves par (school_id, classe) (actifs).

    On tire par paire école+classe (et non par classe toutes écoles confondues)
    afin que chaque superviseur voie les élèves de son école dans le tirage,
    même quand plusieurs écoles ont des classes portant le même nom.
    """
    pairs_result = await db.execute(
        select(Eleve.school_id, Eleve.classe)
        .where(Eleve.statut == "actif")
        .distinct()
    )
    pairs = [(r[0], r[1]) for r in pairs_result.all() if r[0] and r[1]]

    selected_ids: list[uuid.UUID] = []
    for school_id, cls in pairs:
        eleves_result = await db.execute(
            select(Eleve.id).where(
                Eleve.statut == "actif",
                Eleve.school_id == school_id,
                Eleve.classe == cls,
            )
        )
        ids = [r[0] for r in eleves_result.all()]
        if nb_par_classe == 0 or nb_par_classe >= len(ids):
            selected_ids.extend(ids)
        else:
            selected_ids.extend(random.sample(ids, nb_par_classe))
    return selected_ids


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SujetOut])
async def list_sujets(db: DB, _: AdminUser) -> list[SujetOut]:
    """Liste tous les sujets d'évaluation avec leurs statistiques."""
    sujets_result = await db.execute(
        select(EvaluationSujet).order_by(EvaluationSujet.created_at.desc())
    )
    sujets = sujets_result.scalars().all()

    result = []
    for s in sujets:
        count_result = await db.execute(
            select(func.count()).where(EvaluationTirage.sujet_id == s.id)
        )
        nb_tirages = count_result.scalar_one() or 0
        evalues_result = await db.execute(
            select(func.count()).where(
                EvaluationTirage.sujet_id == s.id,
                EvaluationTirage.resultat.isnot(None),
            )
        )
        nb_evalues = evalues_result.scalar_one() or 0
        result.append(SujetOut(
            id=str(s.id),
            titre=s.titre,
            description=s.description,
            nb_eleves_par_classe=s.nb_eleves_par_classe,
            session_id=str(s.session_id) if s.session_id else None,
            nb_tirages=nb_tirages,
            nb_evalues=nb_evalues,
            created_at=s.created_at.isoformat(),
        ))
    return result


@router.post("", response_model=SujetOut, status_code=status.HTTP_201_CREATED)
async def create_sujet(body: SujetCreate, db: DB, current_user: AdminUser) -> SujetOut:
    """
    Crée un sujet d'évaluation et déclenche le tirage aléatoire d'élèves.
    """
    if not body.titre.strip():
        raise HTTPException(status_code=422, detail="Le titre est obligatoire.")
    if body.nb_eleves_par_classe < 0:
        raise HTTPException(status_code=422, detail="nb_eleves_par_classe doit être >= 0.")

    # Session active courante
    session_result = await db.execute(
        select(ProgramSession)
        .where(ProgramSession.status == SessionStatus.active)
        .order_by(ProgramSession.date_debut.desc())
        .limit(1)
    )
    active_session = session_result.scalars().first()

    now = datetime.now(timezone.utc)
    sujet = EvaluationSujet(
        id=uuid.uuid4(),
        titre=body.titre.strip(),
        description=(body.description or "").strip() or None,
        nb_eleves_par_classe=body.nb_eleves_par_classe,
        session_id=active_session.id if active_session else None,
        created_by=current_user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(sujet)
    await db.flush()

    # Tirage aléatoire
    eleve_ids = await _random_eleves(db, body.nb_eleves_par_classe)
    for eid in eleve_ids:
        db.add(EvaluationTirage(
            id=uuid.uuid4(),
            sujet_id=sujet.id,
            eleve_id=eid,
            created_at=now,
            updated_at=now,
        ))

    await db.commit()
    await db.refresh(sujet)

    return SujetOut(
        id=str(sujet.id),
        titre=sujet.titre,
        description=sujet.description,
        nb_eleves_par_classe=sujet.nb_eleves_par_classe,
        session_id=str(sujet.session_id) if sujet.session_id else None,
        nb_tirages=len(eleve_ids),
        nb_evalues=0,
        created_at=sujet.created_at.isoformat(),
    )


@router.get("/{sujet_id}", response_model=SujetDetail)
async def get_sujet(sujet_id: uuid.UUID, db: DB, _: AdminUser) -> SujetDetail:
    """Détail d'un sujet avec la liste complète des tirages et résultats."""
    sujet = (await db.execute(
        select(EvaluationSujet).where(EvaluationSujet.id == sujet_id)
    )).scalar_one_or_none()
    if sujet is None:
        raise HTTPException(status_code=404, detail="Sujet introuvable.")

    tirages_result = await db.execute(
        select(EvaluationTirage)
        .options(
            selectinload(EvaluationTirage.eleve).selectinload(Eleve.school),
            selectinload(EvaluationTirage.superviseur),
        )
        .where(EvaluationTirage.sujet_id == sujet_id)
        .order_by(EvaluationTirage.created_at)
    )
    tirages = tirages_result.scalars().all()

    tirage_items = []
    for t in tirages:
        e = t.eleve
        tirage_items.append(TirageOut(
            id=str(t.id),
            eleve_id=str(t.eleve_id),
            eleve_nom=e.nom if e else "—",
            eleve_prenom=e.prenom if e else None,
            eleve_classe=e.classe if e else None,
            eleve_school=e.school.name if e and e.school else None,
            superviseur_id=str(t.superviseur_id) if t.superviseur_id else None,
            superviseur_nom=t.superviseur.name if t.superviseur else None,
            resultat=t.resultat,
            commentaire=t.commentaire,
            date_eval=t.date_eval.isoformat() if t.date_eval else None,
            audio_filename=t.audio_filename,
            created_at=t.created_at.isoformat(),
        ))

    nb_evalues = sum(1 for t in tirages if t.resultat is not None)

    return SujetDetail(
        id=str(sujet.id),
        titre=sujet.titre,
        description=sujet.description,
        nb_eleves_par_classe=sujet.nb_eleves_par_classe,
        session_id=str(sujet.session_id) if sujet.session_id else None,
        nb_tirages=len(tirages),
        nb_evalues=nb_evalues,
        created_at=sujet.created_at.isoformat(),
        tirages=tirage_items,
    )


@router.delete("/{sujet_id}")
async def delete_sujet(sujet_id: uuid.UUID, db: DB, _: AdminUser) -> Response:
    """Supprime un sujet (et ses tirages en cascade)."""
    sujet = (await db.execute(
        select(EvaluationSujet).where(EvaluationSujet.id == sujet_id)
    )).scalar_one_or_none()
    if sujet is None:
        raise HTTPException(status_code=404, detail="Sujet introuvable.")
    await db.delete(sujet)
    await db.commit()
    return Response(status_code=204)
