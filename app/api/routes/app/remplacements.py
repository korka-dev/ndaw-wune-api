"""Endpoint App mobile — Remplacement d'élève par le tuteur.

Route :
  POST /app/remplacements

Le tuteur choisit un titulaire de sa classe et un remplaçant déjà recensé
dans la même classe (Base NWV 2026 / RCT) — plus de saisie libre de nom.
Effet immédiat : le titulaire sortant passe en statut "inactif", le
remplaçant est promu "Titulaire" (il apparaît désormais dans les listes du
tuteur). L'action est aussi journalisée dans l'historique des modifications
(audit log) afin de remonter sur le dashboard admin, puisque cette route
n'est pas sous /admin/ (seul préfixe surveillé automatiquement par le
middleware d'audit).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import DB, TeacherUser
from app.core.redis import invalidate_sync_caches
from app.models.audit_log import AuditLog
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.schemas.eleve_remplacement import EleveRemplacementCreate, EleveRemplacementResponse

router = APIRouter(prefix="/remplacements", tags=["App — Remplacement élève"])


def _nom_complet(e: Eleve) -> str:
    return f"{e.prenom} {e.nom}".strip() if e.prenom else e.nom


@router.post("", response_model=EleveRemplacementResponse, status_code=201)
async def create_remplacement(
    body: EleveRemplacementCreate, current_user: TeacherUser, db: DB
) -> EleveRemplacementResponse:
    ancien = await db.get(Eleve, body.ancien_eleve_id)
    if ancien is None:
        raise HTTPException(status_code=404, detail="Élève à remplacer introuvable.")
    if ancien.school_id != current_user.school_id:
        raise HTTPException(status_code=403, detail="Cet élève n'appartient pas à votre école.")
    if (ancien.statut_selection or "").strip().lower() == "remplaçant":
        raise HTTPException(status_code=400, detail="Seul un titulaire peut être remplacé.")

    nouveau = await db.get(Eleve, body.nouveau_eleve_id)
    if nouveau is None:
        raise HTTPException(status_code=404, detail="Remplaçant introuvable.")
    if (
        nouveau.school_id != current_user.school_id
        or nouveau.classe != ancien.classe
        or nouveau.statut != "actif"
        or (nouveau.statut_selection or "").strip().lower() != "remplaçant"
    ):
        raise HTTPException(
            status_code=409,
            detail="Ce remplaçant n'est plus disponible pour cette classe. Synchronisez et réessayez.",
        )

    ancien_nom = _nom_complet(ancien)
    nouveau_nom = _nom_complet(nouveau)

    ancien.statut = "inactif"
    nouveau.statut_selection = "Titulaire"

    remplacement = EleveRemplacement(
        ancien_eleve_id=ancien.id,
        ancien_eleve_nom=ancien_nom,
        nouveau_eleve_id=nouveau.id,
        nouveau_eleve_nom=nouveau_nom,
        motif=body.motif.strip(),
        teacher_id=current_user.id,
        school_id=current_user.school_id,
        classe=ancien.classe,
    )
    db.add(remplacement)

    db.add(AuditLog(
        user_id=current_user.id,
        user_name=current_user.name,
        user_role=current_user.role.value,
        action="create",
        entity="Remplacements élève",
        method="POST",
        path="/api/v1/app/remplacements",
        description=(
            f"Remplacement — {ancien_nom} → {nouveau_nom} "
            f"(classe {ancien.classe}) — motif : {body.motif.strip()}"
        ),
    ))

    await db.commit()

    # Le payload de /app/sync est mis en cache Redis pendant une heure : sans
    # cette invalidation, le tuteur continuait de recevoir son ancienne liste
    # d'élèves — l'élève remplacé toujours présent, le nouveau absent — et le
    # remplacement semblait n'avoir jamais été enregistré.
    await invalidate_sync_caches()

    await db.refresh(remplacement)
    return remplacement
