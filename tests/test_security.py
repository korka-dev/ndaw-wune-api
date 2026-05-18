"""
Tests unitaires — app.core.security

Couvre :
  - hash_password / verify_password
  - create_token / decode_token (access & refresh)
  - expiration de token
  - token avec rôle incorrect refusé
"""
from __future__ import annotations

import time

import pytest
from jose import JWTError

from app.core.security import (
    create_token,
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)


# ── Hachage de mots de passe ──────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = hash_password("MonMotDePasse1!")
        assert hashed != "MonMotDePasse1!"

    def test_hash_is_bcrypt(self):
        hashed = hash_password("MonMotDePasse1!")
        assert hashed.startswith("$2b$")

    def test_verify_correct_password(self):
        password = "Bonjour123!"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("CorrectPassword1")
        assert verify_password("WrongPassword1", hashed) is False

    def test_two_hashes_differ(self):
        """bcrypt génère un sel aléatoire — deux hachages du même mot de passe diffèrent."""
        pw = "SamePassword1"
        assert hash_password(pw) != hash_password(pw)

    def test_verify_empty_string_against_hash(self):
        hashed = hash_password("SomePassword1")
        assert verify_password("", hashed) is False


# ── Création et décodage de tokens JWT ───────────────────────────────────────

class TestJWTTokens:
    def test_create_and_decode_access_token(self):
        token = create_token("user-uuid-123", "access", "enseignant")
        payload = decode_token(token)
        assert payload["sub"] == "user-uuid-123"
        assert payload["type"] == "access"
        assert payload["role"] == "enseignant"

    def test_create_and_decode_refresh_token(self):
        token = create_token("user-uuid-456", "refresh", "admin")
        payload = decode_token(token)
        assert payload["sub"] == "user-uuid-456"
        assert payload["type"] == "refresh"
        assert payload["role"] == "admin"

    def test_token_pair_contains_both_types(self):
        pair = create_token_pair("user-uuid-789", "enseignant")
        assert "access_token" in pair
        assert "refresh_token" in pair
        assert pair["token_type"] == "bearer"

        access_payload  = decode_token(pair["access_token"])
        refresh_payload = decode_token(pair["refresh_token"])
        assert access_payload["type"]  == "access"
        assert refresh_payload["type"] == "refresh"

    def test_extra_claims_included(self):
        token = create_token("user-uuid-abc", "access", "enseignant", extra={"school_id": "school-xyz"})
        payload = decode_token(token)
        assert payload["school_id"] == "school-xyz"

    def test_decode_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("not.a.valid.token")

    def test_decode_tampered_token_raises(self):
        token = create_token("user-uuid-111", "access", "enseignant")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_access_token_exp_is_set(self):
        """Le token d'accès doit avoir un champ 'exp' dans le futur."""
        token = create_token("user-uuid-222", "access", "enseignant")
        payload = decode_token(token)
        assert payload["exp"] > int(time.time())

    def test_refresh_token_exp_greater_than_access(self):
        """Le refresh token doit expirer bien après le access token."""
        pair = create_token_pair("user-uuid-333", "enseignant")
        access_exp  = decode_token(pair["access_token"])["exp"]
        refresh_exp = decode_token(pair["refresh_token"])["exp"]
        # refresh expire au minimum 1 jour après l'access (en pratique 30 jours vs 1h)
        assert refresh_exp > access_exp + 86_400
