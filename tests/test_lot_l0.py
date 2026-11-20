"""Lot L0 du rapport V2 : reset public fermé (APP-01), évaluateur en lecture
seule et mot de passe hors du PATCH général (APP-02), mot de passe minimal à
la création (APP-08), détection d'une clé JWT publiée (APP-04)."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes.auth import reset_password
from app.core.config import Settings
from app.core.deps import is_evaluateur, require_admin
from app.models.user import UserRole
from app.schemas.auth import ResetPasswordRequest
from app.schemas.user import UserCreate, UserUpdate


def _req(method: str):
    return SimpleNamespace(method=method)


def _user(role, title=None):
    return SimpleNamespace(role=role, title=title)


class TestPublicResetClosed:
    async def test_reset_refused_for_every_account(self):
        body = ResetPasswordRequest(
            identifier="770000000", new_password="Nouveau123", confirm_password="Nouveau123"
        )
        # Le décorateur du limiteur exige une vraie Request : on appelle la
        # fonction d'origine qu'il enveloppe.
        fn = getattr(reset_password, "__wrapped__", reset_password)
        with pytest.raises(HTTPException) as e:
            await fn(request=None, body=body)
        assert e.value.status_code == 410


class TestEvaluateurReadOnly:
    def test_detection(self):
        assert is_evaluateur(_user(UserRole.coordonnateur, "evaluateur"))
        assert is_evaluateur(_user(UserRole.coordonnateur, " Evaluateur "))
        assert not is_evaluateur(_user(UserRole.coordonnateur))
        assert not is_evaluateur(_user(UserRole.admin, "evaluateur"))

    async def test_evaluateur_can_read(self):
        ev = _user(UserRole.coordonnateur, "evaluateur")
        assert await require_admin(_req("GET"), ev) is ev

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    async def test_evaluateur_cannot_write(self, method):
        with pytest.raises(HTTPException) as e:
            await require_admin(_req(method), _user(UserRole.coordonnateur, "evaluateur"))
        assert e.value.status_code == 403

    async def test_coordonnateur_and_admin_can_write(self):
        for u in (_user(UserRole.coordonnateur), _user(UserRole.admin)):
            assert await require_admin(_req("POST"), u) is u

    async def test_terrain_roles_refused(self):
        for role in (UserRole.enseignant, UserRole.superviseur):
            with pytest.raises(HTTPException):
                await require_admin(_req("GET"), _user(role))


class TestPasswordNotInGeneralUpdate:
    def test_password_field_ignored(self):
        upd = UserUpdate(name="X", password="pirate123")
        assert "password" not in upd.model_dump(exclude_none=True)


class TestCreatePasswordPolicy:
    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(name="A", phone="770000000", password="court", role=UserRole.enseignant)

    def test_empty_password_means_server_default(self):
        u = UserCreate(name="A", phone="770000000", password="  ", role=UserRole.enseignant)
        assert u.password is None


class TestCompromisedSecretKey:
    def test_random_key_not_flagged(self):
        s = Settings(SECRET_KEY="y" * 64)
        assert not s.secret_key_is_compromised
