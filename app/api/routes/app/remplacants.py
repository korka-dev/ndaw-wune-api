"""Endpoint App mobile — Ajout d'un remplaçant par le tuteur.

Route :
  POST /app/remplacants

Jusqu'ici la liste des remplaçants disponibles (utilisée par
POST /app/remplacements) venait uniquement de la Base NWV 2026 (RCT),
importée par l'administrateur. Cette route laisse le tuteur recenser
lui-même un remplaçant supplémentaire pour une de ses classes — un enfant
qui rejoint l'école en cours d'année, par exemple — sans attendre une
correction côté dashboard.

L'élève est créé avec `statut_selection="remplaçant"` : il rejoint
immédiatement le vivier utilisé par l'étape 2 de l'écran de remplacement,
au même titre que les remplaçants importés.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.deps import DB, TeacherUser
from app.core.redis import invalidate_sync_caches
from app.services.audit_service import journaliser
from app.models.eleve import Eleve
from app.schemas.eleve import EleveResponse
from app.schemas.eleve_remplacant import EleveRemplacantCreate

router = APIRouter(prefix="/remplacants", tags=["App — Remplaçants"])


@router.post("", response_model=EleveResponse, status_code=201)
async def create_remplacant(
    body: EleveRemplacantCreate, current_user: TeacherUser, db: DB
) -> EleveResponse:
    # APP-24 : un tuteur sans classe affectée ne peut plus créer d'élève dans
    # n'importe quelle classe ; comparaison normalisée comme /app/sync.
    def _norm(c: str | None) -> str:
        return " ".join((c or "").strip().split()).lower()

    classes_tuteur = {_norm(c) for c in (current_user.classes or []) if c and c.strip()}
    if not current_user.school_id or _norm(body.classe) not in classes_tuteur:
        raise HTTPException(
            status_code=403,
            detail="Cette classe ne fait pas partie de vos classes.",
        )

    eleve = Eleve(
        nom=body.nom,
        prenom=body.prenom,
        classe=body.classe,
        genre=body.genre,
        statut="actif",
        statut_selection="remplaçant",
        school_id=current_user.school_id,
    )
    db.add(eleve)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Un élève avec ce nom existe déjà dans cette classe.",
        )

    journaliser(
        db, acteur=current_user, action="create", entity="Remplaçants élève",
        path="/api/v1/app/remplacants",
        description=f"Remplaçant ajouté — {eleve.prenom or ''} {eleve.nom} (classe {eleve.classe})".strip(),
        cible_type="eleve", cible_id=eleve.id,
    )

    await db.commit()

    # Sans cette invalidation, le remplaçant fraîchement ajouté n'apparaît
    # dans /app/sync qu'une heure plus tard (cache Redis).
    await invalidate_sync_caches()

    await db.refresh(eleve)
    return eleve
