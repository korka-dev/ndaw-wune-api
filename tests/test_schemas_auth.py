"""
Tests unitaires — app.schemas.auth

Couvre :
  - LoginRequest : normalisation téléphone, e-mail, champ vide
  - ChangePasswordRequest : force du mot de passe, correspondance
  - ResetPasswordRequest : idem + champ identifier
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
)


# ── LoginRequest ──────────────────────────────────────────────────────────────

class TestLoginRequest:
    def test_email_lowercased(self):
        req = LoginRequest(identifier="User@Example.COM", password="secret")
        assert req.identifier == "user@example.com"

    def test_email_stripped(self):
        req = LoginRequest(identifier="  admin@ared.sn  ", password="secret")
        assert req.identifier == "admin@ared.sn"

    def test_phone_with_country_code_221(self):
        req = LoginRequest(identifier="221770000000", password="secret")
        assert req.identifier == "770000000"

    def test_phone_with_plus_221(self):
        req = LoginRequest(identifier="+221770000000", password="secret")
        assert req.identifier == "770000000"

    def test_phone_with_00221(self):
        req = LoginRequest(identifier="00221770000000", password="secret")
        assert req.identifier == "770000000"

    def test_phone_local_9digits(self):
        req = LoginRequest(identifier="770000000", password="secret")
        assert req.identifier == "770000000"

    def test_phone_with_spaces(self):
        req = LoginRequest(identifier="77 000 00 00", password="secret")
        assert req.identifier == "770000000"

    def test_empty_password_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            LoginRequest(identifier="user@example.com", password="")
        assert "vide" in str(exc_info.value).lower()


# ── ChangePasswordRequest ─────────────────────────────────────────────────────

class TestChangePasswordRequest:
    def test_valid_password(self):
        req = ChangePasswordRequest(new_password="NouveauMdp1", confirm_password="NouveauMdp1")
        assert req.new_password == "NouveauMdp1"

    def test_too_short_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ChangePasswordRequest(new_password="Ab1", confirm_password="Ab1")
        assert "8" in str(exc_info.value)

    def test_no_uppercase_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ChangePasswordRequest(new_password="nouveaumdp1", confirm_password="nouveaumdp1")
        assert "majuscule" in str(exc_info.value).lower()

    def test_no_digit_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ChangePasswordRequest(new_password="NouveauMdpSans", confirm_password="NouveauMdpSans")
        assert "chiffre" in str(exc_info.value).lower()

    def test_passwords_mismatch_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            ChangePasswordRequest(new_password="NouveauMdp1", confirm_password="AutreMdp1")
        assert "correspondent" in str(exc_info.value).lower()

    def test_empty_confirm_is_ok(self):
        """confirm_password est optionnel (peut être omis côté mobile)."""
        req = ChangePasswordRequest(new_password="NouveauMdp1")
        assert req.confirm_password == ""


# ── ResetPasswordRequest ──────────────────────────────────────────────────────

class TestResetPasswordRequest:
    def test_valid_reset(self):
        req = ResetPasswordRequest(
            identifier="user@example.com",
            new_password="ResetMdp1",
            confirm_password="ResetMdp1",
        )
        assert req.identifier == "user@example.com"
        assert req.new_password == "ResetMdp1"

    def test_phone_normalized(self):
        req = ResetPasswordRequest(
            identifier="+221770000001",
            new_password="ResetMdp1",
            confirm_password="ResetMdp1",
        )
        assert req.identifier == "770000001"

    def test_mismatch_raises(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                identifier="user@example.com",
                new_password="ResetMdp1",
                confirm_password="DifferentMdp1",
            )

    def test_weak_password_raises(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                identifier="user@example.com",
                new_password="weak",
                confirm_password="weak",
            )
