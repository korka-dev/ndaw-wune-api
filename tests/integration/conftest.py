"""
Tests d'intégration : vraie base PostgreSQL migrée + vrai Redis (APP-44).

Activés uniquement si INTEGRATION=1 (CI, ou en local avec les services) :
    INTEGRATION=1 DATABASE_URL=postgresql+asyncpg://… REDIS_URL=redis://… pytest tests/integration
La base doit être migrée (`alembic upgrade head`). Les données créées sont
fictives et portent un préfixe unique par test.
"""
from __future__ import annotations

import os
import random
import uuid

import httpx
import pytest

if os.environ.get("INTEGRATION") != "1":
    pytest.skip("tests d'intégration désactivés (INTEGRATION != 1)", allow_module_level=True)

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.core.redis import close_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.eleve import Eleve  # noqa: E402
from app.models.school import School  # noqa: E402
from app.models.user import User, UserRole, UserStatus  # noqa: E402

PASSWORD = "MotDePasse-Test-123"


@pytest.fixture(autouse=True)
async def _isolate_connections():
    """Chaque test a sa boucle : on libère pool SQL et client Redis à la fin."""
    yield
    await engine.dispose()
    await close_redis()


@pytest.fixture
async def client():
    # Adresse IP distincte par test : les limites par IP (login) ne se cumulent pas.
    # Adresse aléatoire : les compteurs du limiteur persistent dans Redis.
    ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    transport = httpx.ASGITransport(app=app, client=(ip, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def tag() -> str:
    return uuid.uuid4().hex[:8]


async def create_user(role: UserRole, tag: str, **extra) -> User:
    async with AsyncSessionLocal() as s:
        u = User(
            name=f"Test {role.value} {tag}",
            email=f"{role.value}.{tag}.{uuid.uuid4().hex[:4]}@test.invalid",
            password_hash=hash_password(PASSWORD),
            role=role,
            status=UserStatus.actif,
            must_change_password=extra.pop("must_change_password", False),
            **extra,
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u


async def create_school(tag: str) -> School:
    async with AsyncSessionLocal() as s:
        sc = School(name=f"Ecole test {tag}")
        s.add(sc)
        await s.commit()
        await s.refresh(sc)
        return sc


async def create_eleve(school: School, classe: str, tag: str, selection: str) -> Eleve:
    async with AsyncSessionLocal() as s:
        e = Eleve(
            nom=f"Eleve{uuid.uuid4().hex[:6]}", prenom=tag, classe=classe,
            school_id=school.id, statut="actif", statut_selection=selection,
        )
        s.add(e)
        await s.commit()
        await s.refresh(e)
        return e


async def login(client: httpx.AsyncClient, user: User) -> dict:
    r = await client.post("/api/v1/auth/login", json={"identifier": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
