"""
Endpoints App mobile — Évaluations superviseur (sujets + tirages).

Routes :
  GET  /app/supervisor/evaluation-sujets            → sujets actifs avec élèves du superviseur
  POST /app/supervisor/evaluation-tirages/{id}/submit → soumettre résultat + audio
"""
from __future__ import annotations

import logging
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
from app.core.langue import langue_matches
from app.core.upload_utils import ALLOWED_AUDIO_EXTENSIONS, check_extension_allowed, read_upload_capped
from app.models.eleve import Eleve
from app.models.evaluation_resultat import EvaluationResultat
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.user import User
from app.services.supervisor_service import get_supervised_teacher_ids, resolve_supervisor_langue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supervisor", tags=["App — Superviseur Évaluations Sujets"])


# ── Schémas ───────────────────────────────────────────────────────────────────

class TirageAppOut(BaseModel):
    tirage_id: str
    eleve_id: str
    eleve_nom: str
    eleve_prenom: Optional[str] = None
    eleve_genre: Optional[str] = None
    eleve_classe: str
    # Enseignant de l'élève — le superviseur évalue enseignant par enseignant.
    teacher_id: Optional[str] = None
    teacher_nom: Optional[str] = None
    present: Optional[bool] = None
    resultat: Optional[str] = None
    commentaire: Optional[str] = None
    audio_url: Optional[str] = None
    date_eval: Optional[str] = None


class SujetAppOut(BaseModel):
    id: str
    titre: str
    description: Optional[str] = None
    langue: Optional[str] = None
    nb_eleves_par_classe: int
    created_at: str
    eleves: list[TirageAppOut] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _audio_url(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    return f"/api/v1/app/supervisor/evaluation-audio/{filename}"


async def _get_supervisor_classe_map(
    db, supervisor: User
) -> dict[tuple[uuid.UUID, str], tuple[str, str]]:
    """(école, classe) → (teacher_id, nom de l'enseignant) pour les enseignants supervisés.

    Le superviseur travaille enseignant par enseignant : l'app a besoin de
    rattacher chaque élève tiré à son enseignant pour présenter le pointage par
    classe. Si deux enseignants partagent la même classe dans la même école, le
    premier par ordre alphabétique l'emporte (cas marginal, évite un doublon).
    """
    teacher_ids = await get_supervised_teacher_ids(db, supervisor)
    if not teacher_ids:
        return {}
    teachers = (await db.execute(
        select(User).where(User.id.in_(teacher_ids)).order_by(User.name)
    )).scalars().all()
    classe_map: dict[tuple[uuid.UUID, str], tuple[str, str]] = {}
    for t in teachers:
        if t.school_id and t.classes:
            for cls in t.classes:
                classe_map.setdefault((t.school_id, cls), (str(t.id), t.name or "Enseignant"))
    return classe_map


async def _get_supervisor_school_classe_pairs(db, supervisor: User) -> list[tuple[uuid.UUID, str]]:
    """Paires (école, classe) couvertes par les enseignants supervisés."""
    return list((await _get_supervisor_classe_map(db, supervisor)).keys())


async def _get_owned_tirage(db, tirage_id: uuid.UUID, current_user: User) -> EvaluationTirage:
    """
    Charge le tirage et vérifie qu'il appartient à un élève d'une classe
    supervisée par cet utilisateur — sinon 404 (pas 403, pour ne pas
    confirmer l'existence du tirage à quelqu'un qui n'y a pas droit).
    """
    tirage = (await db.execute(
        select(EvaluationTirage)
        .options(selectinload(EvaluationTirage.eleve))
        .where(EvaluationTirage.id == tirage_id)
    )).scalar_one_or_none()
    if tirage is None or tirage.eleve is None:
        raise HTTPException(status_code=404, detail="Tirage introuvable.")

    allowed_pairs = await _get_supervisor_school_classe_pairs(db, current_user)
    e = tirage.eleve
    if not any(e.school_id == sid and e.classe == cls for sid, cls in allowed_pairs):
        raise HTTPException(status_code=404, detail="Tirage introuvable.")
    return tirage


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
    classe_map = await _get_supervisor_classe_map(db, current_user)
    if not classe_map:
        return []

    # Langue d'enseignement du superviseur : seuls les sujets de cette langue
    # (ou sans langue = toutes langues) lui sont proposés.
    #
    # La comparaison passe par canonical_langue : les langues sont saisies en
    # texte libre et les orthographes divergent d'une table à l'autre
    # ("Pulaar"/"poular", "Sérère"/"seereer"…). L'égalité stricte utilisée
    # auparavant ne faisait correspondre que « wolof », écrit identiquement
    # partout — les autres langues ne voyaient donc aucun sujet.
    langue_superviseur = await resolve_supervisor_langue(db, current_user)

    sujets = (await db.execute(
        select(EvaluationSujet).order_by(EvaluationSujet.created_at.desc())
    )).scalars().all()

    if langue_superviseur:
        sujets = [
            s for s in sujets
            if not s.langue or langue_matches(s.langue, langue_superviseur)
        ]

    if not sujets:
        logger.warning(
            "[EvaluationSujets] Aucun sujet pour le superviseur %s (langue=%s).",
            current_user.id, langue_superviseur,
        )
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
            teacher = classe_map.get((e.school_id, e.classe))
            if teacher is not None:
                eleves_app.append(TirageAppOut(
                    tirage_id=str(t.id),
                    eleve_id=str(e.id),
                    eleve_nom=e.nom,
                    eleve_prenom=e.prenom,
                    eleve_genre=e.genre,
                    eleve_classe=e.classe or "",
                    teacher_id=teacher[0],
                    teacher_nom=teacher[1],
                    present=t.present,
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
                langue=sujet.langue,
                nb_eleves_par_classe=sujet.nb_eleves_par_classe,
                created_at=sujet.created_at.isoformat(),
                eleves=sorted(eleves_app, key=lambda x: x.eleve_classe + x.eleve_nom),
            ))

    return result


class TiragePresenceEntry(BaseModel):
    tirage_id: str
    present:   bool


class TiragePresenceIn(BaseModel):
    entries: list[TiragePresenceEntry]


@router.post("/evaluation-tirages/presences", status_code=status.HTTP_200_OK)
async def set_tirages_presence(
    body: TiragePresenceIn,
    current_user: SuperviseurUser,
    db: DB,
) -> dict:
    """
    Marque en lot la présence/absence des élèves tirés au sort, avant de
    démarrer l'évaluation. Les élèves absents ne seront pas évalués.
    """
    if not body.entries:
        raise HTTPException(status_code=422, detail="La liste de présences est vide.")

    allowed_pairs = await _get_supervisor_school_classe_pairs(db, current_user)

    updated = 0
    for entry in body.entries:
        try:
            tid = uuid.UUID(entry.tirage_id)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"tirage_id invalide : {entry.tirage_id}")

        tirage = (await db.execute(
            select(EvaluationTirage)
            .options(selectinload(EvaluationTirage.eleve))
            .where(EvaluationTirage.id == tid)
        )).scalar_one_or_none()
        if tirage is None or tirage.eleve is None:
            continue

        # Ignore silencieusement les tirages hors du périmètre de ce
        # superviseur (élève d'une classe non supervisée) — évite qu'un
        # tirage_id deviné/observé ailleurs permette d'écrire des données
        # sur un élève dont on n'a pas la charge.
        e = tirage.eleve
        if not any(e.school_id == sid and e.classe == cls for sid, cls in allowed_pairs):
            continue

        tirage.present = entry.present
        tirage.superviseur_id = current_user.id
        tirage.updated_at = datetime.now(timezone.utc)
        updated += 1

    await db.commit()
    return {"status": "ok", "updated": updated}


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
    tirage = await _get_owned_tirage(db, tirage_id, current_user)

    if resultat not in ("reussi", "intermediaire", "pas_reussi", "acquis", "a_aider"):
        raise HTTPException(
            status_code=422,
            detail="Résultat invalide : 'reussi', 'intermediaire' ou 'pas_reussi'.",
        )

    # Sauvegarde de l'audio
    if audio and audio.filename:
        check_extension_allowed(audio.filename, ALLOWED_AUDIO_EXTENSIONS)
        content = await read_upload_capped(audio)
        suffix = Path(audio.filename).suffix or ".m4a"
        audio_filename = f"eval_audio_{tirage_id}{suffix}"
        uploads_dir = Path(settings.UPLOADS_DIR)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        (uploads_dir / audio_filename).write_bytes(content)
        tirage.audio_filename = audio_filename

    now = datetime.now(timezone.utc)
    aujourdhui = date.today()
    commentaire_net = (commentaire or "").strip() or None

    # Historique : une ligne par jour d'évaluation. Le même élève est évalué à
    # chaque passage du superviseur ; sans cet enregistrement daté, chaque
    # nouvelle évaluation écraserait la précédente et la progression serait
    # perdue. Réévaluer le même jour corrige la ligne du jour.
    existante = (
        await db.execute(
            select(EvaluationResultat).where(
                EvaluationResultat.tirage_id == tirage.id,
                EvaluationResultat.date_eval == aujourdhui,
            )
        )
    ).scalar_one_or_none()

    if existante is not None:
        existante.resultat       = resultat
        existante.commentaire    = commentaire_net
        existante.superviseur_id = current_user.id
        existante.updated_at     = now
        if tirage.audio_filename:
            existante.audio_filename = tirage.audio_filename
    else:
        db.add(EvaluationResultat(
            id=uuid.uuid4(),
            tirage_id=tirage.id,
            superviseur_id=current_user.id,
            date_eval=aujourdhui,
            resultat=resultat,
            commentaire=commentaire_net,
            audio_filename=tirage.audio_filename,
            created_at=now,
            updated_at=now,
        ))

    # Le tirage conserve le dernier résultat : dashboard admin et app affichent
    # l'état courant sans changement.
    tirage.resultat = resultat
    tirage.commentaire = commentaire_net
    tirage.superviseur_id = current_user.id
    tirage.date_eval = aujourdhui
    tirage.updated_at = now

    await db.commit()
    return {"status": "ok", "tirage_id": str(tirage_id), "resultat": resultat, "date_eval": aujourdhui.isoformat()}


@router.get("/evaluation-audio/{filename}")
async def get_audio(filename: str, db: DB, _: SuperviseurUser):
    """Sert un fichier audio d'évaluation."""
    from fastapi.responses import FileResponse
    path = Path(settings.UPLOADS_DIR) / filename
    if not path.exists() or not filename.startswith("eval_audio_"):
        raise HTTPException(status_code=404, detail="Audio introuvable.")
    return FileResponse(str(path), media_type="audio/mpeg", filename=filename)
