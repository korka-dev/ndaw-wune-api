"""
Endpoints App mobile — Évaluations superviseur (sujets + tirages).

Routes :
  GET  /app/supervisor/evaluation-sujets            → sujets actifs avec élèves du superviseur
  POST /app/supervisor/evaluation-tirages/{id}/submit → soumettre résultat + audio
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import DB, SuperviseurUser
from app.models.eleve import Eleve
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.user import User

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur Évaluations Sujets"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class TirageAppOut(BaseModel):
    tirage_id: str
    eleve_id: str
    eleve_nom: str
    eleve_prenom: Optional[str] = None
    eleve_genre: Optional[str] = None
    eleve_classe: str
    resultat: Optional[str] = None
    commentaire: Optional[str] = None
    audio_url: Optional[str] = None
    date_eval: Optional[str] = None


class SujetAppOut(BaseModel):
    id: str
    titre: str
    description: Optional[str] = None
    nb_eleves_par_classe: int
    created_at: str
    eleves: list[TirageAppOut] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _audio_url(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    return f"/api/v1/app/supervisor/evaluation-audio/{filename}"


async def _get_supervisor_teacher_ids(supervisor: User) -> list[uuid.UUID]:
    """Retourne les UUIDs des enseignants assignés à ce superviseur."""
    ids: list[uuid.UUID] = []
    for s in (supervisor.classes or []):
        try:
            ids.append(uuid.UUID(str(s)))
        except (ValueError, AttributeError):
            pass
    return ids


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/evaluation-sujets", response_model=list[SujetAppOut])
async def list_evaluation_sujets(
    current_user: SuperviseurUser,
    db: DB,
) -> list[SujetAppOut]:
    """
    Retourne les sujets d'évaluation avec les élèves tirés au sort
    qui appartiennent aux classes supervisées par ce superviseur.
    """
    teacher_ids = await _get_supervisor_teacher_ids(current_user)
    if not teacher_ids:
        return []

    # Récupère les enseignants avec leurs classes
    teachers = (await db.execute(
        select(User).where(User.id.in_(teacher_ids))
    )).scalars().all()

    # Construit le mapping (school_id, classe) → teacher
    school_classe_pairs: list[tuple[uuid.UUID, str]] = []
    for t in teachers:
        if t.school_id and t.classes:
            for cls in t.classes:
                school_classe_pairs.append((t.school_id, cls))

    if not school_classe_pairs:
        return []

    # Récupère tous les sujets
    sujets = (await db.execute(
        select(EvaluationSujet).order_by(EvaluationSujet.created_at.desc())
    )).scalars().all()

    if not sujets:
        return []

    # Pour chaque sujet, récupère les tirages dont l'élève est dans les classes supervisées
    result: list[SujetAppOut] = []

    for sujet in sujets:
        tirages_result = await db.execute(
            select(EvaluationTirage)
            .options(selectinload(EvaluationTirage.eleve))
            .where(EvaluationTirage.sujet_id == sujet.id)
        )
        tirages = tirages_result.scalars().all()

        # Filtre : garde uniquement les élèves des classes supervisées
        eleves_app: list[TirageAppOut] = []
        for t in tirages:
            e = t.eleve
            if e is None:
                continue
            if any(e.school_id == sid and e.classe == cls for sid, cls in school_classe_pairs):
                eleves_app.append(TirageAppOut(
                    tirage_id=str(t.id),
                    eleve_id=str(e.id),
                    eleve_nom=e.nom,
                    eleve_prenom=e.prenom,
                    eleve_genre=e.genre,
                    eleve_classe=e.classe or "",
                    resultat=t.resultat,
                    commentaire=t.commentaire,
                    audio_url=_audio_url(t.audio_filename),
                    date_eval=t.date_eval.isoformat() if t.date_eval else None,
                ))

        if eleves_app:  # N'inclure le sujet que s'il y a des élèves assignés
            result.append(SujetAppOut(
                id=str(sujet.id),
                titre=sujet.titre,
                description=sujet.description,
                nb_eleves_par_classe=sujet.nb_eleves_par_classe,
                created_at=sujet.created_at.isoformat(),
                eleves=sorted(eleves_app, key=lambda x: x.eleve_classe + x.eleve_nom),
            ))

    return result


@router.post("/evaluation-tirages/{tirage_id}/submit", status_code=status.HTTP_200_OK)
async def submit_tirage(
    tirage_id: uuid.UUID,
    current_user: SuperviseurUser,
    db: DB,
    resultat: str = Form(...),
    commentaire: Optional[str] = Form(default=None),
    audio: Optional[UploadFile] = File(default=None),
) -> dict:
    """
    Soumet le résultat d'évaluation pour un tirage, avec enregistrement audio optionnel.
    """
    tirage = (await db.execute(
        select(EvaluationTirage)
        .options(selectinload(EvaluationTirage.eleve))
        .where(EvaluationTirage.id == tirage_id)
    )).scalar_one_or_none()
    if tirage is None:
        raise HTTPException(status_code=404, detail="Tirage introuvable.")

    if resultat not in ("acquis", "a_aider"):
        raise HTTPException(status_code=422, detail="Résultat invalide : 'acquis' ou 'a_aider'.")

    # Sauvegarde de l'audio
    if audio and audio.filename:
        content = await audio.read()
        suffix = Path(audio.filename).suffix or ".m4a"
        audio_filename = f"eval_audio_{tirage_id}{suffix}"
        uploads_dir = Path(settings.UPLOADS_DIR)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        (uploads_dir / audio_filename).write_bytes(content)
        tirage.audio_filename = audio_filename

    now = datetime.now(timezone.utc)
    tirage.resultat = resultat
    tirage.commentaire = (commentaire or "").strip() or None
    tirage.superviseur_id = current_user.id
    tirage.date_eval = date.today()
    tirage.updated_at = now

    await db.commit()
    return {"status": "ok", "tirage_id": str(tirage_id), "resultat": resultat}


@router.get("/evaluation-audio/{filename}")
async def get_audio(filename: str, db: DB, _: SuperviseurUser):
    """Sert un fichier audio d'évaluation."""
    from fastapi.responses import FileResponse
    path = Path(settings.UPLOADS_DIR) / filename
    if not path.exists() or not filename.startswith("eval_audio_"):
        raise HTTPException(status_code=404, detail="Audio introuvable.")
    return FileResponse(str(path), media_type="audio/mpeg", filename=filename)
