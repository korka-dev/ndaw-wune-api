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
from sqlalchemy import select

from app.core.deps import DB, TeacherUser
from app.core.redis import invalidate_sync_caches
from app.services.audit_service import journaliser
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.models.school import School
from app.schemas.eleve_remplacement import EleveRemplacementCreate, EleveRemplacementResponse
from app.services.school_service import get_matching_school_ids

router = APIRouter(prefix="/remplacements", tags=["App — Remplacement élève"])


def _norm_classe(c: str | None) -> str:
    return " ".join((c or "").strip().split()).lower()


def _nom_complet(e: Eleve) -> str:
    return f"{e.prenom} {e.nom}".strip() if e.prenom else e.nom


@router.post("", response_model=EleveRemplacementResponse, status_code=201)
async def create_remplacement(
    body: EleveRemplacementCreate, current_user: TeacherUser, db: DB
) -> EleveRemplacementResponse:
    # APP-24 : même périmètre que la liste reçue par le tuteur (/app/sync) —
    # écoles de même nom normalisé ET classes affectées au tuteur — appliqué aux
    # DEUX élèves. Les lignes sont verrouillées (FOR UPDATE) : deux demandes
    # simultanées ne peuvent pas consommer le même remplaçant.
    school = await db.get(School, current_user.school_id) if current_user.school_id else None
    if school is None or not current_user.classes:
        raise HTTPException(status_code=403, detail="Aucune classe ne vous est affectée.")
    school_ids = await get_matching_school_ids(db, school)
    mes_classes = {_norm_classe(c) for c in current_user.classes if c and c.strip()}

    def _dans_mon_perimetre(e: Eleve) -> bool:
        return e.school_id in school_ids and _norm_classe(e.classe) in mes_classes

    ids = sorted({body.ancien_eleve_id, body.nouveau_eleve_id}, key=str)
    if len(ids) != 2:
        raise HTTPException(status_code=400, detail="Le remplaçant doit être un autre élève.")
    rows = (await db.execute(
        select(Eleve).where(Eleve.id.in_(ids)).order_by(Eleve.id).with_for_update()
    )).scalars().all()
    par_id = {e.id: e for e in rows}

    ancien = par_id.get(body.ancien_eleve_id)
    if ancien is None or not _dans_mon_perimetre(ancien):
        # 404 : on ne confirme pas l'existence d'un élève hors périmètre.
        raise HTTPException(status_code=404, detail="Élève à remplacer introuvable dans vos classes.")
    if (ancien.statut_selection or "").strip().lower() == "remplaçant":
        raise HTTPException(status_code=400, detail="Seul un titulaire peut être remplacé.")
    if ancien.statut != "actif":
        raise HTTPException(
            status_code=409,
            detail="Cet élève a déjà été remplacé ou n'est plus actif. Synchronisez et réessayez.",
        )

    nouveau = par_id.get(body.nouveau_eleve_id)
    if nouveau is None or not _dans_mon_perimetre(nouveau):
        raise HTTPException(status_code=404, detail="Remplaçant introuvable dans vos classes.")
    if (
        _norm_classe(nouveau.classe) != _norm_classe(ancien.classe)
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

    journaliser(
        db, acteur=current_user, action="create", entity="Remplacements élève",
        path="/api/v1/app/remplacements",
        description=f"Remplacement — {ancien_nom} → {nouveau_nom} (classe {ancien.classe})",
        cible_type="eleve", cible_id=ancien.id, motif=body.motif,
    )

    await db.commit()

    # Le payload de /app/sync est mis en cache Redis pendant une heure : sans
    # cette invalidation, le tuteur continuait de recevoir son ancienne liste
    # d'élèves — l'élève remplacé toujours présent, le nouveau absent — et le
    # remplacement semblait n'avoir jamais été enregistré.
    await invalidate_sync_caches()

    await db.refresh(remplacement)
    return remplacement
