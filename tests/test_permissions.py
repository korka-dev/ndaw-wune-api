"""Contrôle acteur / cible / opération (fiche APP-02) et garde-fous d'upload (APP-39)."""
import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from app.core.upload_utils import read_upload_capped
from app.models.user import UserRole
from app.services.user_service import assert_can_manage


def _user(role, title=None):
    return SimpleNamespace(role=role, title=title)


ADMIN = _user(UserRole.admin)
COORD = _user(UserRole.coordonnateur)


class TestAssertCanManage:
    def test_coordonnateur_cannot_touch_admin_through_teacher_route(self):
        with pytest.raises(HTTPException) as e:
            assert_can_manage(COORD, ADMIN, (UserRole.enseignant,))
        assert e.value.status_code == 404

    def test_admin_account_never_manageable_even_by_admin_on_terrain_routes(self):
        with pytest.raises(HTTPException) as e:
            assert_can_manage(ADMIN, ADMIN, (UserRole.enseignant, UserRole.superviseur))
        assert e.value.status_code == 404

    def test_wrong_role_for_route_is_not_found(self):
        with pytest.raises(HTTPException) as e:
            assert_can_manage(ADMIN, _user(UserRole.superviseur), (UserRole.enseignant,))
        assert e.value.status_code == 404

    def test_teacher_manageable_by_coordonnateur(self):
        assert_can_manage(COORD, _user(UserRole.enseignant), (UserRole.enseignant,))

    def test_plain_coordonnateur_only_manageable_by_admin(self):
        target = _user(UserRole.coordonnateur)
        with pytest.raises(HTTPException) as e:
            assert_can_manage(COORD, target, (UserRole.superviseur, UserRole.coordonnateur))
        assert e.value.status_code == 403
        assert_can_manage(ADMIN, target, (UserRole.superviseur, UserRole.coordonnateur))

    def test_evaluateur_manageable_by_coordonnateur_but_only_on_evaluateur_route(self):
        ev = _user(UserRole.coordonnateur, "evaluateur")
        assert_can_manage(COORD, ev, (UserRole.coordonnateur,), evaluateur_only=True)
        with pytest.raises(HTTPException):
            assert_can_manage(ADMIN, _user(UserRole.coordonnateur), (UserRole.coordonnateur,), evaluateur_only=True)


class TestReadUploadCapped:
    async def test_small_file_ok(self):
        f = UploadFile(file=io.BytesIO(b"abc" * 10), filename="a.csv")
        assert await read_upload_capped(f, max_size=1024) == b"abc" * 10

    async def test_too_large_rejected_with_413(self):
        f = UploadFile(file=io.BytesIO(b"x" * (3 * 1024 * 1024)), filename="big.csv")
        with pytest.raises(HTTPException) as e:
            await read_upload_capped(f, max_size=1024 * 1024)
        assert e.value.status_code == 413
