"""
Couche service pour les séances (timer) et les rapports enseignants.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.journal import pile, resume_erreur
from app.services.audit_service import journaliser
from app.models.seance import RapportProf, Seance, SeanceStatus
from app.schemas.seance import (
    RapportCreate, SeanceComplete, SeanceFinish, SeanceMissedReport, SeancePauseBody, check_coherence_seance,
    SeanceRecovery, SeanceResumeBody, SeanceStart,
)

logger = logging.getLogger(__name__)

# Une séance restée "en_cours" plus longtemps que ça sans timer_stop est
# considérée comme oubliée par l'enseignant (app fermée, batterie morte…)
# et clôturée automatiquement.
AUTO_CLOSE_AFTER = timedelta(hours=4)


async def close_stale_seances(db: AsyncSession, teacher_id: uuid.UUID | None = None) -> int:
    """Clôture automatiquement les séances "en_cours" depuis plus de 4 h.

    Appelée par la tâche de fond périodique (app/main.py) et au démarrage d'une
    séance par le tuteur — jamais par la consultation d'une page (APP-21).

    AUCUNE durée n'est inférée : l'heure réelle de fin est inconnue, donc
    finished_at et duree_minutes restent NULL et cloture_mode = automatique.
    Si l'arrêt réel du minuteur arrive plus tard depuis le téléphone, il est
    accepté et remplace cet état (cloture_mode = tuteur_tardive).
    """
    seuil = datetime.now(timezone.utc) - AUTO_CLOSE_AFTER
    q = select(Seance).where(
        Seance.status == SeanceStatus.en_cours,
        Seance.started_at.isnot(None),
        Seance.started_at < seuil,
    )
    if teacher_id is not None:
        q = q.where(Seance.teacher_id == teacher_id)
    rows = (await db.execute(q.with_for_update(skip_locked=True))).scalars().all()

    for seance in rows:
        seance.status        = SeanceStatus.terminee
        seance.auto_closed   = True
        seance.cloture_mode  = "automatique"
        seance.finished_at   = None
        seance.duree_minutes = None

    if rows:
        await db.flush()
        logger.info("close_stale_seances: %d séance(s) clôturée(s) automatiquement.", len(rows))
    return len(rows)


def _seance_summary(seance: Seance) -> dict:
    return {
        "id":               str(seance.id),
        "classe":           seance.classe,
        "matiere":          seance.matiere,
        "date_seance":      seance.date_seance.isoformat() if seance.date_seance else None,
        "started_at":       seance.started_at.isoformat() if seance.started_at else None,
        "client_seance_id": seance.client_seance_id,
    }


async def _get_by_client_id(db: AsyncSession, teacher_id: uuid.UUID, client_id: str) -> Seance | None:
    return (await db.execute(
        select(Seance).where(Seance.teacher_id == teacher_id, Seance.client_seance_id == client_id)
    )).scalar_one_or_none()


# ── Séances ────────────────────────────────────────────────────────────────────

async def report_missed_seance(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    body: SeanceMissedReport,
) -> Seance:
    """
    Crée une séance avec status=manquee pour traçabilité.
    Idempotent : si une séance manquée existe déjà pour ce segment ce jour-là, retourne l'existante.
    """
    logger.info(
        "report_missed_seance: teacher=%s segment=%s classe=%s",
        teacher_id, body.planning_segment_id, body.classe,
    )
    # Idempotence : un seul enregistrement manqué par segment par jour
    if body.planning_segment_id:
        from sqlalchemy import func as sqlfunc
        existing = (await db.execute(
            select(Seance).where(
                Seance.teacher_id           == teacher_id,
                Seance.planning_segment_id  == body.planning_segment_id,
                Seance.status               == SeanceStatus.manquee,
                sqlfunc.date(Seance.date_seance) == sqlfunc.date(body.date_seance),
            )
        )).scalar_one_or_none()
        if existing:
            return existing

    await verifier_campagne(db, teacher_id, body.session_id, body.planning_segment_id,
                            body.classe, body.date_seance, tardif=True)

    seance = Seance(
        teacher_id          = teacher_id,
        session_id          = body.session_id,
        planning_segment_id = body.planning_segment_id,
        classe              = body.classe,
        matiere             = body.matiere,
        date_seance         = body.date_seance,
        status              = SeanceStatus.manquee,
        started_at          = None,
        finished_at         = None,
        duree_minutes       = 0,
    )
    db.add(seance)
    await db.flush()
    await db.refresh(seance)
    return seance


async def get_active_seance(db: AsyncSession, teacher_id: uuid.UUID) -> Seance | None:
    result = await db.execute(
        select(Seance).where(
            Seance.teacher_id == teacher_id,
            Seance.status     == SeanceStatus.en_cours,
        )
    )
    return result.scalar_one_or_none()


# Délai de transmission accepté après la fin d'une campagne pour une séance
# faite hors ligne (APP-22, envois tardifs) — à valider par l'ARED.
DELAI_ENVOI_TARDIF_JOURS = 14


def _norm_classe(v: Optional[str]) -> str:
    return " ".join((v or "").split()).lower()


async def verifier_campagne(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    session_id: uuid.UUID,
    planning_segment_id: Optional[uuid.UUID],
    classe: Optional[str],
    date_seance: datetime,
    *,
    tardif: bool,
) -> None:
    """Contrôles serveur d'une séance (rapport V2, APP-22).

    - campagne existante et non archivée ; active pour un démarrage en direct ;
      pour un envoi tardif (hors ligne), la date de la séance doit tomber dans
      la campagne (+ délai de transmission) ;
    - tuteur affecté à la campagne quand il a des affectations ;
    - créneau appartenant à la campagne (et au tuteur s'il est nominatif) ;
    - classe du créneau, sinon une classe du tuteur.
    Refus explicite (409/422) : rien n'est enregistré.
    """
    from app.models.planning import PlanningSegment
    from app.models.session import ProgramSession, SessionStatus, TeacherSession
    from app.models.user import User

    def refus(code: str, message: str, http: int = 422):
        return HTTPException(status_code=http, detail={"code": code, "message": message})

    session = await db.get(ProgramSession, session_id)
    if session is None:
        raise refus("CAMPAGNE_INCONNUE", "Campagne introuvable.")
    if session.archived_at is not None:
        raise refus("CAMPAGNE_ARCHIVEE", "Cette campagne est archivée : aucune séance ne peut y être ajoutée.",
                    status.HTTP_409_CONFLICT)
    if session.status != SessionStatus.active:
        jour = date_seance.date() if isinstance(date_seance, datetime) else date_seance
        dans_periode = session.date_debut <= jour <= session.date_fin + timedelta(days=DELAI_ENVOI_TARDIF_JOURS)
        if not (tardif and dans_periode):
            raise refus("CAMPAGNE_INACTIVE", "Cette campagne n'est pas active : synchronisez l'application.",
                        status.HTTP_409_CONFLICT)

    affectations = set((await db.execute(
        select(TeacherSession.session_id).where(TeacherSession.teacher_id == teacher_id)
    )).scalars().all())
    if affectations and session_id not in affectations:
        raise refus("CAMPAGNE_NON_AFFECTEE", "Vous n'êtes pas affecté(e) à cette campagne.", status.HTTP_403_FORBIDDEN)

    classe_attendue: Optional[str] = None
    if planning_segment_id is not None:
        seg = await db.get(PlanningSegment, planning_segment_id)
        if seg is None or seg.session_id != session_id or (seg.teacher_id and seg.teacher_id != teacher_id):
            raise refus("CRENEAU_INVALIDE", "Ce créneau n'appartient pas à votre planning de cette campagne.")
        classe_attendue = seg.classe

    c = _norm_classe(classe)
    if c and c != "—":
        if classe_attendue:
            if c != _norm_classe(classe_attendue):
                raise refus("CLASSE_NON_AFFECTEE", f"La classe « {classe} » ne correspond pas au créneau.")
        else:
            teacher = await db.get(User, teacher_id)
            classes = {_norm_classe(x) for x in (teacher.classes or [])} if teacher else set()
            if classes and c not in classes:
                raise refus("CLASSE_NON_AFFECTEE", f"La classe « {classe} » ne vous est pas affectée.")


async def start_seance(db: AsyncSession, teacher_id: uuid.UUID, body: SeanceStart) -> Seance:
    """Démarre le timer. Lève 409 si une séance est déjà en cours."""
    logger.info(
        "start_seance: teacher=%s session=%s classe=%s planning_segment=%s",
        teacher_id, body.session_id, body.classe, body.planning_segment_id,
    )
    try:
        # Récupération d'une séance oubliée : la clôture ne dépend plus de
        # l'ouverture d'une page admin — elle est faite ici, au moment où le
        # tuteur en aurait besoin (la séance est marquée auto_closed).
        # Rejeu (APP-16) : la même séance client renvoie la séance déjà créée.
        if body.client_seance_id:
            deja = await _get_by_client_id(db, teacher_id, body.client_seance_id)
            if deja is not None:
                return deja

        await verifier_campagne(db, teacher_id, body.session_id, body.planning_segment_id,
                                body.classe, body.date_seance, tardif=False)

        await close_stale_seances(db, teacher_id)
        existing = await get_active_seance(db, teacher_id)
        if existing:
            # Conflit explicite (APP-18/21) : le téléphone compare l'identité
            # de la séance active avant toute réutilisation, et peut proposer
            # au tuteur de la reprendre ou de la clôturer comme oubliée.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code":    "SEANCE_ACTIVE",
                    "message": "Une séance est déjà en cours. Terminez-la ou clôturez-la avant d'en démarrer une nouvelle.",
                    "active":  _seance_summary(existing),
                },
            )

        seance = Seance(teacher_id=teacher_id, **body.model_dump())
        db.add(seance)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            deja = await _get_by_client_id(db, teacher_id, body.client_seance_id) if body.client_seance_id else None
            if deja is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Impossible de créer la séance. Réessayez ou contactez le support.",
                )
            return deja
        except SQLAlchemyError as exc:
            await db.rollback()
            logger.error("DB Error lors de start_seance teacher=%s : %s", teacher_id, resume_erreur(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de créer la séance. Réessayez ou contactez le support.",
            )
        await db.refresh(seance)
        return seance

    except HTTPException:
        raise  # laisser passer les HTTPException normales (409, 400...)
    except Exception as exc:
        # Attrape toute exception inattendue et l'expose en 400 (pas 500)
        logger.error(
            "Erreur inattendue dans start_seance teacher=%s : %s | %s",
            teacher_id, resume_erreur(exc), pile(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Erreur inattendue lors du démarrage de la séance. Réessayez ou contactez le support.",
        )


async def _get_seance_owned(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
) -> Seance:
    """Récupère une séance en vérifiant l'ownership. Lève 404 si introuvable."""
    result = await db.execute(
        select(Seance).where(
            Seance.id         == seance_id,
            Seance.teacher_id == teacher_id,
        )
    )
    seance = result.scalar_one_or_none()
    if seance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance introuvable.")
    return seance


async def record_pause(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeancePauseBody,
) -> Seance:
    """Enregistre un événement de mise en pause dans la séance."""
    seance = await _get_seance_owned(db, seance_id, teacher_id)
    if seance.status != SeanceStatus.en_cours:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette séance n'est plus en cours.")

    pauses = list(seance.pauses or [])
    pauses.append({"paused_at": body.paused_at.isoformat(), "resumed_at": None})
    seance.pauses = pauses

    await db.flush()
    await db.refresh(seance)
    return seance


async def record_resume(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeanceResumeBody,
) -> Seance:
    """Ferme le dernier événement de pause (resumed_at) et recalcule total_paused_minutes."""
    seance = await _get_seance_owned(db, seance_id, teacher_id)
    if seance.status != SeanceStatus.en_cours:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette séance n'est plus en cours.")

    pauses = list(seance.pauses or [])

    # Fermer la dernière pause ouverte
    for p in reversed(pauses):
        if p.get("resumed_at") is None:
            p["resumed_at"] = body.resumed_at.isoformat()
            break

    # Recalculer le total des pauses (en minutes)
    from datetime import datetime as dt
    total_ms = 0
    for p in pauses:
        if p.get("paused_at") and p.get("resumed_at"):
            try:
                pa = dt.fromisoformat(p["paused_at"])
                ra = dt.fromisoformat(p["resumed_at"])
                total_ms += max(0, int((ra - pa).total_seconds() * 1000))
            except Exception:
                pass
    seance.pauses               = pauses
    seance.total_paused_minutes = max(0, round(total_ms / 60000))

    await db.flush()
    await db.refresh(seance)
    return seance


async def finish_seance(
    db: AsyncSession,
    seance_id: uuid.UUID,
    teacher_id: uuid.UUID,
    body: SeanceFinish,
) -> Seance:
    """Clôture le timer. Fusionne les pauses offline si fournies."""
    logger.info(
        "finish_seance: seance_id=%s teacher=%s finished_at=%s duree=%s pauses=%d",
        seance_id, teacher_id, body.finished_at, body.duree_minutes, len(body.pauses),
    )
    try:
        seance = await _get_seance_owned(db, seance_id, teacher_id)
        if seance.status == SeanceStatus.terminee and not seance.auto_closed and seance.cloture_mode != "recuperation":
            # Rejeu après perte de la réponse (APP-16) : même arrêt → succès.
            if _same_instant(seance.finished_at, body.finished_at) and seance.duree_minutes == body.duree_minutes:
                return seance
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code":    "SEANCE_DEJA_CLOTUREE",
                    "message": "Cette séance a déjà été clôturée avec d'autres valeurs.",
                },
            )
        if seance.status not in (SeanceStatus.en_cours, SeanceStatus.terminee):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cette séance n'est plus en cours.",
            )

        def _refus(msg: str):
            return HTTPException(status_code=422, detail=msg)

        check_coherence_seance(seance.started_at, body.finished_at, body.duree_minutes,
                               body.nb_eleves_presents, seance.nb_eleves_total, exc=_refus)

        # En cours, ou clôturée sans durée (automatique / récupération) : l'arrêt
        # réel du minuteur, même tardif, est la seule durée mesurée (APP-21).
        tardive = seance.status == SeanceStatus.terminee
        seance.finished_at        = body.finished_at
        seance.duree_minutes      = body.duree_minutes
        seance.nb_eleves_presents = body.nb_eleves_presents
        seance.status             = SeanceStatus.terminee
        seance.auto_closed        = False
        seance.cloture_mode       = "tuteur_tardive" if tardive else "tuteur"

        # Fusionner les pauses offline si le serveur n'en a pas encore
        if body.pauses and not seance.pauses:
            from sqlalchemy.orm.attributes import flag_modified
            seance.pauses = [p.model_dump() for p in body.pauses]
            flag_modified(seance, "pauses")
        if body.total_paused_minutes is not None and seance.total_paused_minutes is None:
            seance.total_paused_minutes = body.total_paused_minutes

        try:
            await db.flush()
        except SQLAlchemyError as exc:
            await db.rollback()
            logger.error("DB Error lors de finish_seance seance_id=%s : %s", seance_id, resume_erreur(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de clôturer la séance. Réessayez ou contactez le support.",
            )
        await db.refresh(seance)
        return seance

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Erreur inattendue dans finish_seance seance_id=%s : %s | %s",
            seance_id, resume_erreur(exc), pile(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Erreur inattendue lors de la clôture. Réessayez ou contactez le support.",
        )


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((a - b).total_seconds()) < 1


async def complete_seance(db: AsyncSession, teacher_id: uuid.UUID, body: SeanceComplete) -> Seance:
    """Enregistre en une fois une séance faite hors ligne (APP-16/18).

    - Idempotent par client_seance_id : un rejeu renvoie la séance existante.
    - Indépendant de la séance active sur le serveur : on ne réutilise JAMAIS
      une autre séance (ancien comportement sur 409).
    - Si la séance existe déjà (démarrée en ligne puis oubliée / clôturée
      automatiquement), l'arrêt mesuré la complète.
    """
    seance = await _get_by_client_id(db, teacher_id, body.client_seance_id)
    if seance is not None:
        if seance.status == SeanceStatus.en_cours or (
            seance.status == SeanceStatus.terminee and seance.duree_minutes is None
        ):
            return await finish_seance(db, seance.id, teacher_id, SeanceFinish(
                finished_at=body.finished_at, duree_minutes=body.duree_minutes,
                nb_eleves_presents=body.nb_eleves_presents, pauses=body.pauses,
                total_paused_minutes=body.total_paused_minutes,
            ))
        return seance

    await verifier_campagne(db, teacher_id, body.session_id, body.planning_segment_id,
                            body.classe, body.date_seance, tardif=True)
    start = body.model_dump(include=set(SeanceStart.model_fields))
    seance = Seance(
        teacher_id=teacher_id,
        **start,
        status=SeanceStatus.terminee,
        cloture_mode="tuteur",
        finished_at=body.finished_at,
        duree_minutes=body.duree_minutes,
        nb_eleves_presents=body.nb_eleves_presents,
        pauses=[p.model_dump() for p in body.pauses],
        total_paused_minutes=body.total_paused_minutes,
    )
    try:
        async with db.begin_nested():
            db.add(seance)
            await db.flush()
    except IntegrityError:
        deja = await _get_by_client_id(db, teacher_id, body.client_seance_id)
        if deja is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible d'enregistrer la séance. Vérifiez la session et le créneau.",
            )
        return deja
    await db.refresh(seance)
    return seance


async def recover_forgotten_seance(
    db: AsyncSession,
    seance_id: uuid.UUID,
    user,
    body: SeanceRecovery,
) -> Seance:
    """Clôture par le tuteur d'une séance restée ouverte (APP-21).

    Arbitrage tracé dans l'historique ; aucune durée inférée. Si l'arrêt réel
    du minuteur arrive ensuite depuis un téléphone, il reste accepté.
    """
    seance = await _get_seance_owned(db, seance_id, user.id)
    if seance.status != SeanceStatus.en_cours:
        return seance  # déjà clôturée : rejouable sans effet
    seance.status        = SeanceStatus.terminee
    seance.cloture_mode  = "recuperation"
    seance.finished_at   = None
    seance.duree_minutes = None
    journaliser(
        db, acteur=user, action="update", entity="Séance",
        path=f"/api/v1/app/seances/{seance.id}/cloturer-oubli",
        description=(
            f"Séance oubliée clôturée par le tuteur (classe {seance.classe}, "
            f"démarrée {seance.started_at:%d/%m/%Y %H:%M}) — sans durée mesurée"
        ) if seance.started_at else "Séance oubliée clôturée par le tuteur — sans durée mesurée",
        cible_type="seance", cible_id=seance.id, motif=body.motif,
    )
    await db.flush()
    await db.refresh(seance)
    return seance


async def list_teacher_seances(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[Seance]]:
    base = (
        select(Seance)
        .where(Seance.teacher_id == teacher_id)
        .order_by(Seance.date_seance.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items


# ── Rapports ───────────────────────────────────────────────────────────────────

async def submit_rapport(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    body: RapportCreate,
) -> RapportProf:
    logger.info("submit_rapport: teacher=%s seance_id=%s", teacher_id, body.seance_id)
    try:
        # Vérifier l'existence et l'ownership de la séance
        result = await db.execute(select(Seance).where(Seance.id == body.seance_id))
        seance = result.scalar_one_or_none()
        if seance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance introuvable.")
        if seance.teacher_id != teacher_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cette séance ne vous appartient pas.")

        # Un seul rapport par séance (contrainte DB + vérification applicative)
        existing = (
            await db.execute(select(RapportProf).where(RapportProf.seance_id == body.seance_id))
        ).scalar_one_or_none()
        if existing:
            # Rejeu après perte de la réponse (APP-16) : même contenu → succès.
            if (
                existing.contenu == body.contenu
                and (existing.difficultes or None) == (body.difficultes or None)
                and (existing.points_positifs or None) == (body.points_positifs or None)
            ):
                return existing
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un rapport existe déjà pour cette séance.")

        rapport = RapportProf(teacher_id=teacher_id, **body.model_dump())
        db.add(rapport)
        try:
            await db.flush()
        except SQLAlchemyError as exc:
            await db.rollback()
            logger.error("DB Error lors de submit_rapport teacher=%s seance_id=%s : %s", teacher_id, body.seance_id, resume_erreur(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de soumettre le rapport. Réessayez ou contactez le support.",
            )
        await db.refresh(rapport)
        return rapport

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Erreur inattendue dans submit_rapport teacher=%s seance_id=%s : %s | %s",
            teacher_id, body.seance_id, resume_erreur(exc), pile(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Erreur inattendue lors de la soumission du rapport. Réessayez ou contactez le support.",
        )


async def list_teacher_rapports(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[RapportProf]]:
    base = (
        select(RapportProf)
        .where(RapportProf.teacher_id == teacher_id)
        .order_by(RapportProf.created_at.desc())
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items


async def list_admin_rapports(
    db: AsyncSession,
    session_id: uuid.UUID | None,
    teacher_id: uuid.UUID | None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[int, Sequence[RapportProf]]:
    base = select(RapportProf).order_by(RapportProf.created_at.desc())
    if teacher_id:
        base = base.where(RapportProf.teacher_id == teacher_id)
    if session_id:
        base = base.join(Seance, Seance.id == RapportProf.seance_id).where(Seance.session_id == session_id)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return total, items
