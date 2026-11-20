"""Scénarios du rapport V2 rejoués contre l'API réelle (PostgreSQL + Redis)."""
from __future__ import annotations

import asyncio

from app.core.config import settings
from app.models.user import UserRole

from .conftest import bearer, create_eleve, create_school, create_user, login


async def test_reset_public_ferme(client, tag):  # APP-01
    u = await create_user(UserRole.enseignant, tag)
    r = await client.post("/api/v1/auth/reset-password", json={
        "identifier": u.email, "new_password": "Pirate1234", "confirm_password": "Pirate1234",
    })
    assert r.status_code == 410
    await login(client, u)  # l'ancien mot de passe fonctionne toujours


async def test_evaluateur_lecture_seule(client, tag):  # APP-02
    ev = await create_user(UserRole.coordonnateur, tag, title="evaluateur")
    h = bearer(await login(client, ev))
    assert (await client.get("/api/v1/admin/schools", headers=h)).status_code == 200
    r = await client.post("/api/v1/admin/schools", headers=h, json={"name": f"Ecole {tag}"})
    assert r.status_code == 403


async def test_activation_imposee(client, tag):  # APP-08
    u = await create_user(UserRole.enseignant, tag, must_change_password=True)
    r = await client.get("/api/v1/app/sync", headers=bearer(await login(client, u)))
    assert r.status_code == 403 and r.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"


async def test_rotation_refresh(client, tag, monkeypatch):  # APP-10
    u = await create_user(UserRole.enseignant, tag)
    tokens = await login(client, u)
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    # Hors délai de grâce, l'ancien refresh token est refusé.
    monkeypatch.setattr(settings, "REFRESH_ROTATION_GRACE_SECONDS", -1)
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401
    # Le nouveau fonctionne.
    r3 = await client.post("/api/v1/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]})
    assert r3.status_code == 200


async def test_reset_par_admin_ferme_les_sessions(client, tag):  # APP-02/10
    admin = await create_user(UserRole.admin, tag)
    coord = await create_user(UserRole.coordonnateur, tag)
    admin_h = bearer(await login(client, admin))
    coord_tokens = await login(client, coord)
    await asyncio.sleep(1.1)  # tokens_valid_from est à la seconde
    r = await client.post(
        f"/api/v1/admin/users/{coord.id}/reset-password",
        headers=admin_h,
        json={"new_password": "NouveauMdp-456", "force_change": False},
    )
    assert r.status_code == 200, r.text
    # Anciens jetons (accès et refresh) refusés, nouveau mot de passe accepté.
    assert (await client.get("/api/v1/admin/schools", headers=bearer(coord_tokens))).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": coord_tokens["refresh_token"]})
    assert r.status_code == 401
    r = await client.post("/api/v1/auth/login", json={"identifier": coord.email, "password": "NouveauMdp-456"})
    assert r.status_code == 200
    # Un admin ne réinitialise pas son propre compte par cette route.
    r = await client.post(
        f"/api/v1/admin/users/{admin.id}/reset-password", headers=admin_h,
        json={"new_password": "NouveauMdp-789"},
    )
    assert r.status_code == 400


async def test_remplacement_hors_classe_refuse(client, tag):  # APP-24
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CM1 A"])
    autre_titulaire = await create_eleve(school, "CM2 B", tag, "Titulaire")
    autre_remplacant = await create_eleve(school, "CM2 B", tag, "remplaçant")
    h = bearer(await login(client, tuteur))
    r = await client.post("/api/v1/app/remplacements", headers=h, json={
        "ancien_eleve_id": str(autre_titulaire.id), "nouveau_eleve_id": str(autre_remplacant.id),
    })
    assert r.status_code == 404


async def test_remplacement_unique(client, tag):  # APP-24
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["cm1  a"])
    titulaire = await create_eleve(school, "CM1 A", tag, "Titulaire")
    titulaire2 = await create_eleve(school, "CM1 A", tag, "Titulaire")
    remplacant = await create_eleve(school, "CM1 A", tag, "remplaçant")
    h = bearer(await login(client, tuteur))
    r1 = await client.post("/api/v1/app/remplacements", headers=h, json={
        "ancien_eleve_id": str(titulaire.id), "nouveau_eleve_id": str(remplacant.id),
    })
    assert r1.status_code == 201, r1.text
    # Le même remplaçant ne peut pas être consommé une deuxième fois.
    r2 = await client.post("/api/v1/app/remplacements", headers=h, json={
        "ancien_eleve_id": str(titulaire2.id), "nouveau_eleve_id": str(remplacant.id),
    })
    assert r2.status_code == 409


async def test_version_minimale(client, tag, monkeypatch):  # APP-46
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    monkeypatch.setattr(settings, "MIN_APP_VERSION", "2.0.0")
    r = await client.get("/api/v1/app/sync", headers={**h, "X-App-Version": "1.4.0"})
    assert r.status_code == 426 and r.json()["detail"] == "APP_UPDATE_REQUIRED"
    # APK déjà distribués (sans en-tête) : jamais bloqués.
    r = await client.get("/api/v1/app/sync", headers=h)
    assert r.status_code != 426
    r = await client.get("/api/v1/app-version")
    assert r.json()["min_version"] == "2.0.0"
