"""Un superviseur ne doit jamais atterrir dans l'espace tuteur de l'app mobile.

La page « Comptes » du dashboard créait sous le libellé « Superviseur » un compte
de rôle coordonnateur : l'app le rangeait dans l'espace tuteur. /auth/me annonce
désormais l'espace mobile et l'API superviseur accepte ces comptes.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.models.progression_config import ProgressionConfig
from app.models.user import UserRole

from .conftest import bearer, create_school, create_user, login

API = "/api/v1"


async def _me(client, user):
    h = bearer(await login(client, user))
    r = await client.get(f"{API}/auth/me", headers=h)
    assert r.status_code == 200, r.text
    return r.json(), h


@pytest.mark.parametrize("role,title,attendu", [
    (UserRole.enseignant, None, "tuteur"),
    (UserRole.superviseur, None, "superviseur"),
    (UserRole.inspecteur, None, "inspecteur"),
    (UserRole.coordonnateur, "Superviseur", "superviseur"),
    (UserRole.coordonnateur, None, "superviseur"),
    (UserRole.coordonnateur, "evaluateur", None),
    (UserRole.admin, None, None),
])
async def test_espace_mobile_annonce_par_auth_me(client, tag, role, title, attendu):
    me, _ = await _me(client, await create_user(role, tag, title=title))
    assert me["espace_mobile"] == attendu, me


async def test_coordonnateur_superviseur_utilise_l_espace_superviseur(client, tag):
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    sup = await create_user(UserRole.coordonnateur, tag, title="Superviseur",
                            school_id=school.id, classes=[str(tuteur.id)])
    _, h = await _me(client, sup)
    r = await client.get(f"{API}/app/supervisor/sync", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["role"] == "superviseur"
    assert [t["id"] for t in data["assigned_teachers"]] == [str(tuteur.id)]
    # L'espace tuteur lui reste fermé.
    assert (await client.get(f"{API}/app/sync", headers=h)).status_code == 403


async def test_evaluateur_n_a_pas_l_espace_superviseur(client, tag):
    _, h = await _me(client, await create_user(UserRole.coordonnateur, tag, title="evaluateur"))
    assert (await client.get(f"{API}/app/supervisor/sync", headers=h)).status_code == 403


async def test_semaines_du_superviseur_suivent_son_ecole(client, tag):
    """La configuration propre à l'école vaut pour le superviseur comme pour ses tuteurs."""
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id, classes=[str(tuteur.id)])
    async with AsyncSessionLocal() as s:
        s.add(ProgressionConfig(school_id=school.id, session_id=None, nb_semaines=25, nb_jours=3))
        await s.commit()
    try:
        _, h_sup = await _me(client, sup)
        _, h_tut = await _me(client, tuteur)
        r_sup = await client.get(f"{API}/app/supervisor/sync", headers=h_sup)
        r_tut = await client.get(f"{API}/app/sync", headers=h_tut)
        assert r_sup.status_code == 200 and r_tut.status_code == 200, (r_sup.text, r_tut.text)
        assert r_sup.json()["nb_semaines"] == 25
        assert r_tut.json()["nb_semaines"] == 25
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(ProgressionConfig).where(ProgressionConfig.school_id == school.id))
            await s.commit()


# ── Économie de données : 304 quand la synchronisation n'a pas changé ─────────

async def test_sync_tuteur_304_puis_200_apres_modification(client, tag):
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    _, h = await _me(client, tuteur)
    r = await client.get(f"{API}/app/sync", headers=h)
    assert r.status_code == 200 and r.headers.get("etag"), r.text
    etag = r.headers["etag"]
    r = await client.get(f"{API}/app/sync", headers={**h, "If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    # Modification côté admin (nombre de semaines de l'école) → nouveau contenu.
    async with AsyncSessionLocal() as s:
        s.add(ProgressionConfig(school_id=school.id, session_id=None, nb_semaines=25, nb_jours=3))
        await s.commit()
    try:
        # Le bouton « actualiser » de l'app vide le cache serveur du tuteur.
        assert (await client.post(f"{API}/app/sync/invalidate", headers=h)).status_code == 204
        r = await client.get(f"{API}/app/sync", headers={**h, "If-None-Match": etag})
        assert r.status_code == 200 and r.json()["nb_semaines"] == 25
        assert r.headers["etag"] != etag
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(ProgressionConfig).where(ProgressionConfig.school_id == school.id))
            await s.commit()


async def test_sync_superviseur_304(client, tag):
    school = await create_school(tag)
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id)
    _, h = await _me(client, sup)
    r = await client.get(f"{API}/app/supervisor/sync", headers=h)
    assert r.status_code == 200
    r2 = await client.get(f"{API}/app/supervisor/sync", headers={**h, "If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
