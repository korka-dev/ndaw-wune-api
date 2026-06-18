"""Endpoints App mobile — Évaluations élèves par les superviseurs de terrain.

Routes :
  GET  /app/supervisor/eleves        → élèves par classe, pour les enseignants assignés
  GET  /app/supervisor/evaluations   → évaluations soumises par ce superviseur
  POST /app/supervisor/evaluations   → soumettre des évaluations (batch)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.deps import DB, SuperviseurUser
from app.models.eleve import Eleve
from app.models.evaluation_eleve import EvaluationEleve
from app.models.session import ProgramSession, SessionStatus
from app.models.user import User

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class EleveItem(BaseModel):
    id:     str
    nom:    str
    prenom: Optional[str] = None
    genre:  Optional[str] = None
    classe: str

    model_config = {"from_attributes": True}


class ClasseMeta(BaseModel):
    """Métadonnées légères d'une classe — pas d'élèves, seulement le comptage."""
    classe:    str
    nb_eleves: int


class TeacherPayload(BaseModel):
    teacher_id:   str
    teacher_name: str
    classes:      list[ClasseMeta] = []


class ElevesPayload(BaseModel):
    teachers: list[TeacherPayload]


class EvaluationIn(BaseModel):
    eleve_id:    str
    competence:  str
    resultat:    str       # "acquis" | "en_cours" | "a_aider"
    date_eval:   str       # ISO YYYY-MM-DD
    commentaire: Optional[str] = None


class EvaluationBatch(BaseModel):
    evaluations: list[EvaluationIn]


class EvaluationOut(BaseModel):
    id:            str
    eleve_id:      str
    competence:    str
    resultat:      str
    date_eval:     str
    commentaire:   Optional[str] = None
    created_at:    str


class EvaluationsPayload(BaseModel):
    evaluations: list[EvaluationOut]
    total:       int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/eleves", response_model=ElevesPayload)
async def supervisor_eleves(current_user: SuperviseurUser, db: DB) -> ElevesPayload:
    """
    Retourne TOUS les enseignants assignés + métadonnées de leurs classes (comptages uniquement).
    Les élèves détaillés sont chargés à la demande via GET /classe-eleves.
    """
    if not current_user.classes:
        return ElevesPayload(teachers=[])

    teacher_ids: list[uuid.UUID] = []
    for id_str in current_user.classes:
        try:
            teacher_ids.append(uuid.UUID(id_str))
        except ValueError:
            continue

    if not teacher_ids:
        return ElevesPayload(teachers=[])

    teachers_result = await db.execute(
        select(User).where(User.id.in_(teacher_ids))
    )
    teachers = teachers_result.scalars().all()

    result_teachers: list[TeacherPayload] = []

    for teacher in teachers:
        classes_meta: list[ClasseMeta] = []

        if teacher.school_id and teacher.classes:
            for cls in teacher.classes:
                # Un seul COUNT par classe — très léger
                count_result = await db.execute(
                    select(func.count()).where(
                        Eleve.school_id == teacher.school_id,
                        Eleve.classe == cls,
                        Eleve.statut == "actif",
                    )
                )
                nb = count_result.scalar() or 0
                classes_meta.append(ClasseMeta(classe=cls, nb_eleves=nb))

        result_teachers.append(TeacherPayload(
            teacher_id=str(teacher.id),
            teacher_name=teacher.name or "Enseignant sans nom",
            classes=sorted(classes_meta, key=lambda c: c.classe),
        ))

    result_teachers.sort(key=lambda t: t.teacher_name)
    return ElevesPayload(teachers=result_teachers)


@router.get("/classe-eleves", response_model=list[EleveItem])
async def get_classe_eleves(
    current_user: SuperviseurUser,
    db: DB,
    teacher_id: str = Query(...),
    classe: str = Query(...),
) -> list[EleveItem]:
    """
    Charge les élèves d'une classe spécifique, à la demande (lazy loading).
    Vérifie que l'enseignant est bien assigné à ce superviseur.
    """
    # Vérifier que cet enseignant est bien assigné à ce superviseur
    if teacher_id not in (current_user.classes or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Enseignant non assigné.")

    try:
        tid = uuid.UUID(teacher_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="teacher_id invalide.")

    teacher_result = await db.execute(select(User).where(User.id == tid))
    teacher = teacher_result.scalar_one_or_none()
    if not teacher or not teacher.school_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enseignant introuvable.")

    eleve_result = await db.execute(
        select(Eleve)
        .where(
            Eleve.school_id == teacher.school_id,
            Eleve.classe == classe,
            Eleve.statut == "actif",
        )
        .order_by(Eleve.nom)
    )
    return [
        EleveItem(id=str(e.id), nom=e.nom, prenom=e.prenom, genre=e.genre, classe=e.classe)
        for e in eleve_result.scalars().all()
    ]


@router.get("/evaluations", response_model=EvaluationsPayload)
async def list_evaluations(
    current_user: SuperviseurUser,
    db: DB,
    date_debut: Optional[str] = None,
    date_fin:   Optional[str] = None,
    classe:     Optional[str] = None,
) -> EvaluationsPayload:
    """
    Retourne les évaluations soumises par ce superviseur.
    Filtres optionnels : date_debut, date_fin (YYYY-MM-DD), classe.
    """
    q = select(EvaluationEleve).where(
        EvaluationEleve.superviseur_id == current_user.id
    ).order_by(EvaluationEleve.date_eval.desc(), EvaluationEleve.created_at.desc())

    if date_debut:
        try:
            q = q.where(EvaluationEleve.date_eval >= date.fromisoformat(date_debut))
        except ValueError:
            raise HTTPException(status_code=422, detail="date_debut invalide (format YYYY-MM-DD).")
    if date_fin:
        try:
            q = q.where(EvaluationEleve.date_eval <= date.fromisoformat(date_fin))
        except ValueError:
            raise HTTPException(status_code=422, detail="date_fin invalide (format YYYY-MM-DD).")

    rows = (await db.execute(q)).scalars().all()

    # Filtre par classe : jointure post-requête sur eleve.classe
    if classe:
        eleve_ids = {e.eleve_id for e in rows}
        if eleve_ids:
            eleves_result = await db.execute(
                select(Eleve).where(Eleve.id.in_(eleve_ids), Eleve.classe == classe)
            )
            classe_ids = {e.id for e in eleves_result.scalars().all()}
            rows = [r for r in rows if r.eleve_id in classe_ids]
        else:
            rows = []

    items = [
        EvaluationOut(
            id=str(e.id),
            eleve_id=str(e.eleve_id),
            competence=e.competence,
            resultat=e.resultat,
            date_eval=e.date_eval.isoformat(),
            commentaire=e.commentaire,
            created_at=e.created_at.isoformat(),
        )
        for e in rows
    ]
    return EvaluationsPayload(evaluations=items, total=len(items))


RESULTATS_VALIDES = {"acquis", "a_aider"}


@router.post("/evaluations", status_code=status.HTTP_201_CREATED)
async def submit_evaluations(
    body: EvaluationBatch,
    current_user: SuperviseurUser,
    db: DB,
) -> dict:
    """
    Soumet un lot d'évaluations.
    Upsert : si une évaluation (superviseur, élève, compétence, date) existe déjà,
    elle est mise à jour (résultat + commentaire).
    """
    if not body.evaluations:
        raise HTTPException(status_code=422, detail="La liste d'évaluations est vide.")

    # Session active (optionnelle — liée pour le suivi)
    session_result = await db.execute(
        select(ProgramSession)
        .where(ProgramSession.status == SessionStatus.active)
        .order_by(ProgramSession.date_debut.desc())
        .limit(1)
    )
    active_session = session_result.scalars().first()
    session_id = active_session.id if active_session else None

    now = datetime.now(timezone.utc)
    created = updated = 0

    for ev in body.evaluations:
        # Validation
        if ev.resultat not in RESULTATS_VALIDES:
            raise HTTPException(
                status_code=422,
                detail=f"Résultat invalide : '{ev.resultat}'. Valeurs : {RESULTATS_VALIDES}",
            )
        try:
            eleve_uuid = uuid.UUID(ev.eleve_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"eleve_id invalide : {ev.eleve_id}")
        try:
            eval_date = date.fromisoformat(ev.date_eval)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"date_eval invalide : {ev.date_eval}")

        # Chercher doublon existant
        existing = (await db.execute(
            select(EvaluationEleve).where(
                EvaluationEleve.superviseur_id == current_user.id,
                EvaluationEleve.eleve_id       == eleve_uuid,
                EvaluationEleve.competence     == ev.competence,
                EvaluationEleve.date_eval      == eval_date,
            )
        )).scalar_one_or_none()

        if existing:
            existing.resultat    = ev.resultat
            existing.commentaire = ev.commentaire
            existing.updated_at  = now
            updated += 1
        else:
            record = EvaluationEleve(
                id=uuid.uuid4(),
                superviseur_id=current_user.id,
                eleve_id=eleve_uuid,
                session_id=session_id,
                competence=ev.competence,
                resultat=ev.resultat,
                date_eval=eval_date,
                commentaire=ev.commentaire,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            created += 1

    await db.flush()
    return {"created": created, "updated": updated, "total": created + updated}
