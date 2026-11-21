"""Modèle Rapport Journalier — soumis par le tuteur/superviseur depuis l'app mobile."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class RapportJournalier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rapports_journalier"
    # Index présents en base (migrations) et déclarés ici pour que
    # `alembic check` reste sans écart (APP-27).
    __table_args__ = (
        Index("ix_rapports_journalier_created_at", "created_at"),
        Index("ix_rapports_journalier_date_rapport", "date_rapport"),
        Index("ix_rapports_journalier_ecole", "ecole"),
        Index("ix_rapports_journalier_ief", "ief"),
        Index("ix_rapports_journalier_nom_tuteur", "nom_tuteur"),
        # Idempotence (APP-16) : une clé d'opération client par auteur.
        UniqueConstraint("teacher_id", "client_op_id", name="uq_rapports_journalier_teacher_client_op"),
    )

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # ── Données administratives ───────────────────────────────────────────────
    date_rapport: Mapped[date]           = mapped_column(Date, nullable=False)
    ief:          Mapped[str]            = mapped_column(String(200), nullable=False)
    commune:      Mapped[str]            = mapped_column(String(200), nullable=False)
    ecole:        Mapped[str]            = mapped_column(String(255), nullable=False)
    superviseur:  Mapped[str]            = mapped_column(String(255), nullable=False)
    nom_tuteur:   Mapped[str]            = mapped_column(String(255), nullable=False)

    # ── Présences ─────────────────────────────────────────────────────────────
    nb_absences:  Mapped[int]            = mapped_column(Integer, default=0, nullable=False)
    absents:      Mapped[Optional[str]]  = mapped_column(Text, nullable=True)  # JSON list
    semaine:      Mapped[int]            = mapped_column(Integer, nullable=False)
    jour_cours:   Mapped[int]            = mapped_column(Integer, nullable=False)

    # ── Difficultés ───────────────────────────────────────────────────────────
    difficultes:              Mapped[str]            = mapped_column(Text, nullable=False)  # JSON list
    autres_difficultes:       Mapped[Optional[str]]  = mapped_column(Text, nullable=True)
    description_difficultes:  Mapped[Optional[str]]  = mapped_column(Text, nullable=True)

    # ── Observations ─────────────────────────────────────────────────────────
    directeur_venu:   Mapped[bool]          = mapped_column(Boolean, nullable=False)
    besoin_appui:     Mapped[bool]          = mapped_column(Boolean, nullable=False)
    domaines_appui:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    has_observations: Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    commentaires:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Métadonnées ──────────────────────────────────────────────────────────
    soumis_en_offline: Mapped[bool]          = mapped_column(Boolean, default=True, nullable=False)
    photo_classe_url:  Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ancien champ — 1 seule photo
    photos_classe_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # liste JSON — jusqu'à 3 photos
    reponses_questions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON {question_id: réponse} — questions dynamiques configurées par l'admin

    # ── Idempotence (APP-16) ─────────────────────────────────────────────────
    # Clé générée par le téléphone à la saisie ; un rejeu renvoie le rapport
    # déjà créé. L'empreinte du contenu détecte une même clé réutilisée pour
    # un contenu différent (rejetée explicitement).
    client_op_id:   Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    client_op_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ── Campagne (APP-22) ────────────────────────────────────────────────────
    # Session active du tuteur au moment de la réception. NULL pour l'historique
    # antérieur, dont le rattachement reste à arbitrer (pas de déduction).
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("program_sessions.id", ondelete="RESTRICT"), nullable=True, index=True,
    )

    # ── Rattachements et traçabilité analytique (lot L4, Partie II D4/D5) ────
    # Fixés à la RÉCEPTION, jamais recalculés : un changement d'école ou de
    # superviseur en cours de campagne ne réécrit pas les rapports passés.
    # NULL pour les rapports antérieurs (aucun rattachement deviné).
    ecole_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    # Superviseur responsable à la date de réception (NULL si aucun ou plusieurs).
    superviseur_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True,
    )
    # Identifiants des élèves déclarés absents (liste JSON d'UUID). Le champ
    # `absents` (noms) reste la valeur saisie lisible.
    absents_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Inscrits éligibles (élèves actifs des classes du tuteur) à la réception :
    # référence de calcul conservée, distincte des effectifs déclarés.
    effectif_attendu: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Horodatage de la saisie sur le téléphone (submitted_at) ; la réception
    # serveur est created_at. L'écart mesure le retard de transmission.
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Cycle de vie (APP-31) ────────────────────────────────────────────────
    # soumis → corrige (nouvelle version, motif) → annule (motif). Jamais
    # supprimé : chaque état antérieur est conservé dans rapport_journalier_versions.
    statut:        Mapped[str]                 = mapped_column(String(20), nullable=False, default="soumis",
                                                               server_default="soumis", index=True)
    version:       Mapped[int]                 = mapped_column(Integer, nullable=False, default=1, server_default="1")
    statut_motif:  Mapped[Optional[str]]       = mapped_column(Text, nullable=True)
    statut_par_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    statut_at:     Mapped[Optional[datetime]]  = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relations ─────────────────────────────────────────────────────────────
    teacher: Mapped["User"] = relationship("User", foreign_keys=[teacher_id])

    def __repr__(self) -> str:
        return f"<RapportJournalier {self.date_rapport} [{self.ecole}]>"


STATUTS_RAPPORT = ("soumis", "corrige", "annule")


class RapportJournalierVersion(Base, UUIDMixin):
    """État antérieur d'un rapport journalier, figé avant chaque correction ou
    annulation (APP-31). Les photos ne sont pas recopiées (elles restent sur
    le rapport et ne sont pas modifiables)."""
    __tablename__ = "rapport_journalier_versions"
    __table_args__ = (
        UniqueConstraint("rapport_id", "version", name="uq_rapport_journalier_version"),
    )

    rapport_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rapports_journalier.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    version:    Mapped[int]                 = mapped_column(Integer, nullable=False)
    statut:     Mapped[str]                 = mapped_column(String(20), nullable=False)
    contenu:    Mapped[dict]                = mapped_column(JSONB, nullable=False)
    # Ce qui a provoqué le remplacement de cette version.
    action:     Mapped[str]                 = mapped_column(String(20), nullable=False)  # correction | annulation
    motif:      Mapped[str]                 = mapped_column(Text, nullable=False)
    auteur_id:  Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime]            = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
    )


# Règle commune (APP-31) : un rapport annulé reste consultable (liste, export,
# historique) mais ne compte plus dans les indicateurs ni la progression.
RAPPORT_COMPTE = RapportJournalier.statut != "annule"
