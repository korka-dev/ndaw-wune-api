"""
Tests unitaires — schémas métier

Couvre :
  - EleveCreate : validation des champs obligatoires
  - RapportJournalierCreate : construction et valeurs par défaut
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.eleve import EleveCreate, EleveUpdate
from app.schemas.rapport_journalier import RapportJournalierCreate


# ── EleveCreate ───────────────────────────────────────────────────────────────

class TestEleveCreate:
    def test_minimal_valid(self):
        eleve = EleveCreate(nom="Diallo", classe="CI")
        assert eleve.nom == "Diallo"
        assert eleve.classe == "CI"
        assert eleve.prenom is None
        assert eleve.school_id is None
        assert eleve.session_id is None

    def test_full_valid(self):
        school_id  = uuid.uuid4()
        session_id = uuid.uuid4()
        eleve = EleveCreate(
            nom="Diallo",
            prenom="Aminata",
            classe="CP",
            school_id=school_id,
            session_id=session_id,
        )
        assert eleve.prenom == "Aminata"
        assert eleve.school_id == school_id

    def test_empty_nom_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            EleveCreate(nom="", classe="CI")
        assert "vide" in str(exc_info.value).lower()

    def test_whitespace_nom_raises(self):
        with pytest.raises(ValidationError):
            EleveCreate(nom="   ", classe="CI")

    def test_empty_classe_raises(self):
        with pytest.raises(ValidationError):
            EleveCreate(nom="Diallo", classe="")

    def test_nom_stripped(self):
        eleve = EleveCreate(nom="  Diallo  ", classe="CI")
        assert eleve.nom == "Diallo"

    def test_classe_stripped(self):
        eleve = EleveCreate(nom="Diallo", classe="  CE1  ")
        assert eleve.classe == "CE1"


# ── EleveUpdate ──────────────────────────────────────────────────────────────

class TestEleveUpdate:
    def test_all_none_is_valid(self):
        """PATCH partiel : tous les champs optionnels."""
        update = EleveUpdate()
        assert update.nom is None
        assert update.classe is None

    def test_partial_update(self):
        update = EleveUpdate(classe="CE2")
        assert update.classe == "CE2"
        assert update.nom is None


# ── RapportJournalierCreate ───────────────────────────────────────────────────

class TestRapportJournalierCreate:
    def _valid_payload(self) -> dict:
        return {
            "date_rapport":   date.today(),
            "ief":            "IEF Dakar-Plateau",
            "commune":        "Plateau",
            "ecole":          "École Thiong",
            "superviseur":    "M. Ndiaye",
            "nom_tuteur":     "Mme Faye",
            "semaine":        1,
            "jour_cours":     3,
            "difficultes":    "[]",
            "directeur_venu": True,
            "besoin_appui":   False,
        }

    def test_minimal_valid(self):
        rapport = RapportJournalierCreate(**self._valid_payload())
        assert rapport.nb_absences == 0
        assert rapport.soumis_en_offline is True
        assert rapport.has_observations is False
        assert rapport.photo_classe_url is None

    def test_defaults(self):
        rapport = RapportJournalierCreate(**self._valid_payload())
        assert rapport.absents is None
        assert rapport.autres_difficultes is None
        assert rapport.description_difficultes is None
        assert rapport.domaines_appui is None
        assert rapport.commentaires is None

    def test_with_optional_fields(self):
        payload = self._valid_payload()
        payload.update({
            "nb_absences":    3,
            "absents":        '["Moussa", "Awa"]',
            "commentaires":   "Bonne séance malgré les absences.",
            "photo_classe_url": "https://cdn.example.com/photo.jpg",
            "soumis_en_offline": False,
        })
        rapport = RapportJournalierCreate(**payload)
        assert rapport.nb_absences == 3
        assert rapport.soumis_en_offline is False
        assert rapport.commentaires == "Bonne séance malgré les absences."

    def test_missing_required_field_raises(self):
        payload = self._valid_payload()
        del payload["ief"]
        with pytest.raises(ValidationError):
            RapportJournalierCreate(**payload)

    def test_date_rapport_type(self):
        rapport = RapportJournalierCreate(**self._valid_payload())
        assert isinstance(rapport.date_rapport, date)
