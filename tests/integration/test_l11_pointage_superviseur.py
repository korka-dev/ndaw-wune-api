"""Pointage des tuteurs par le superviseur : numéro du jour de cours (1 = Jour 1).

Avant la 1.4.1, l'app envoyait l'index (0 = Jour 1) : Jour 1 était refusé et
l'écran relançait l'envoi en boucle. Le serveur corrige les anciennes versions.
"""
from __future__ import annotations

from datetime import date

from .test_l3_integrite import _terrain

API = "/api/v1"


async def _pointer(client, h, tuteur, jour, version, present=True):
    return await client.post(f"{API}/app/supervisor/presences", headers={**h, "X-App-Version": version}, json={
        "date_jour": date.today().isoformat(), "semaine": 3, "jour_cours": jour,
        "entries": [{"teacher_id": str(tuteur.id), "present": present,
                     "motif": None if present else "Maladie"}]})


async def _jour_enregistre(client, h):
    r = await client.get(f"{API}/app/supervisor/presences", headers=h)
    assert r.status_code == 200
    return r.json()


async def test_ancienne_app_jour_1_accepte_et_corrige(client, tag):
    _, tuteur, _, _, h = await _terrain(client, tag)
    r = await _pointer(client, h, tuteur, 0, "1.3.2")       # ancienne app, « Jour 1 »
    assert r.status_code == 201, r.text
    assert (await _jour_enregistre(client, h))["jour_cours"] == 1
    r = await _pointer(client, h, tuteur, 1, "1.3.2")       # ancienne app, « Jour 2 »
    assert (await _jour_enregistre(client, h))["jour_cours"] == 2


async def test_nouvelle_app_et_modification_du_pointage(client, tag):
    _, tuteur, _, _, h = await _terrain(client, tag)
    r = await _pointer(client, h, tuteur, 1, "1.4.1")
    assert r.status_code == 201, r.text
    assert (await _jour_enregistre(client, h))["jour_cours"] == 1
    # Le superviseur corrige : absent, puis soumet de nouveau → mis à jour.
    r = await _pointer(client, h, tuteur, 3, "1.4.1", present=False)
    assert r.json()["updated"] == 1
    pointage = await _jour_enregistre(client, h)
    assert pointage["jour_cours"] == 3
    assert pointage["entries"] == [{"teacher_id": str(tuteur.id), "present": False, "motif": "Maladie"}]
