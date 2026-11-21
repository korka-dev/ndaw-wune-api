"""Lot L4 — APP-38 / REC11 : liste d'élèves paginée, filtrée et triée côté serveur,
exports complets au-delà de 10 000 élèves (aucun plafond d'écran)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import openpyxl
import pytest
from sqlalchemy import delete, insert

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.school import School
from app.models.user import UserRole

from .conftest import bearer, create_school, create_user, login

API = "/api/v1"
N = 10_050          # au-delà de l'ancien plafond de chargement (10 000)
N_ARCHIVES = 10     # archivés : exclus par défaut


def _nom(tag: str, i: int) -> str:
    return f"N{tag}{i:05d}"


@pytest.fixture
async def gros_effectif(tag):
    """Une école fictive et 10 050 élèves insérés en masse (SQL Core)."""
    school = await create_school(tag)
    now = datetime.now(timezone.utc)
    rows = [
        {
            "nom": _nom(tag, i), "prenom": f"P{i % 7}", "classe": f"CE{1 + i % 2}",
            "genre": "Fille" if i % 2 else "Garçon",
            "statut": "actif", "statut_selection": "Remplaçant" if i % 3 == 0 else "Titulaire",
            "school_id": school.id,
            # Les derniers (dans l'ordre du tri par nom) sont archivés.
            "archived_at": now if i >= N - N_ARCHIVES else None,
        }
        for i in range(N)
    ]
    async with AsyncSessionLocal() as s:
        await s.execute(insert(Eleve), rows)
        await s.commit()
    try:
        yield school
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Eleve).where(Eleve.school_id == school.id))
            await s.execute(delete(School).where(School.id == school.id))
            await s.commit()


async def _admin(client, tag):
    return bearer(await login(client, await create_user(UserRole.admin, tag)))


async def test_liste_serveur_au_dela_de_10000(client, tag, gros_effectif):
    h = await _admin(client, tag)
    base = {"school_id": str(gros_effectif.id)}
    actifs = N - N_ARCHIVES
    url = f"{API}/admin/eleves"

    # Total exact du périmètre filtré, réponse bornée à la page demandée.
    r = await client.get(url, headers=h, params=base | {"limit": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == actifs and len(body["items"]) == 50
    assert body["items"][0]["nom"] == _nom(tag, 0)

    # Taille de page plafonnée par MAX_PAGE_SIZE.
    r = await client.get(url, headers=h, params=base | {"limit": settings.MAX_PAGE_SIZE + 1})
    assert r.status_code == 422

    # Le 10 001e élève (tri par nom) est atteignable par pagination…
    r = await client.get(url, headers=h, params=base | {"skip": 10_000, "limit": 1, "sort": "nom"})
    assert [e["nom"] for e in r.json()["items"]] == [_nom(tag, 10_000)]
    # … et par la recherche serveur.
    r = await client.get(url, headers=h, params={"search": _nom(tag, 10_000)})
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["nom"] == _nom(tag, 10_000)
    # Recherche « nom prénom » combinée.
    r = await client.get(url, headers=h, params=base | {"search": f"{_nom(tag, 10_000)} P{10_000 % 7}"})
    assert r.json()["total"] == 1

    # Tri décroissant stable ; les archivés sont exclus puis réintégrés sur demande.
    r = await client.get(url, headers=h, params=base | {"limit": 1, "order": "desc"})
    assert r.json()["items"][0]["nom"] == _nom(tag, N - N_ARCHIVES - 1)
    r = await client.get(url, headers=h, params=base | {"limit": 1, "archives": "true"})
    assert r.json()["total"] == N

    # Filtres sexe / type / classe appliqués en SQL avec un total exact.
    filles = sum(1 for i in range(actifs) if i % 2)
    remplacants = sum(1 for i in range(actifs) if i % 3 == 0)
    r = await client.get(url, headers=h, params=base | {"sexe": "Fille", "limit": 1})
    assert r.json()["total"] == filles
    r = await client.get(url, headers=h, params=base | {"type": "remplacant", "limit": 1})
    assert r.json()["total"] == remplacants
    r = await client.get(url, headers=h, params=base | {"classe": "CE1", "sexe": "garçon", "limit": 1})
    assert r.json()["total"] == sum(1 for i in range(actifs) if i % 2 == 0)

    # Tri inconnu refusé (liste blanche).
    r = await client.get(url, headers=h, params=base | {"sort": "password_hash"})
    assert r.status_code == 422

    # Compteurs agrégés côté serveur sur le même périmètre.
    r = await client.get(f"{url}/stats", headers=h, params=base)
    assert r.status_code == 200, r.text
    st = r.json()
    assert (st["total"], st["filles"], st["garcons"], st["remplacants"], st["titulaires"]) == (
        actifs, filles, actifs - filles, remplacants, actifs - remplacants)
    assert {g["label"] for g in st["par_classe"]} == {"CE1", "CE2"}
    assert st["par_ecole"] == [{"label": gros_effectif.name, "value": actifs}]


async def test_exports_complets_sans_plafond(client, tag, gros_effectif):
    """REC11 : l'export contient toutes les lignes éligibles, au-delà de 10 000."""
    h = await _admin(client, tag)
    base = {"school_id": str(gros_effectif.id)}
    actifs = N - N_ARCHIVES

    r = await client.get(f"{API}/admin/eleves/export/csv", headers=h, params=base)
    assert r.status_code == 200, r.text
    lignes = list(csv.reader(io.StringIO(r.text)))
    assert lignes[0][0] == "nom"
    assert len(lignes) - 1 == actifs > 10_000
    assert lignes[1][0] == _nom(tag, 0) and lignes[-1][0] == _nom(tag, actifs - 1)

    # Mêmes filtres que la liste.
    filles = sum(1 for i in range(actifs) if i % 2)
    r = await client.get(f"{API}/admin/eleves/export/csv", headers=h, params=base | {"sexe": "Fille"})
    assert len(list(csv.reader(io.StringIO(r.text)))) - 1 == filles
    r = await client.get(f"{API}/admin/eleves/export/csv", headers=h, params=base | {"archives": "true"})
    assert len(list(csv.reader(io.StringIO(r.text)))) - 1 == N

    r = await client.get(f"{API}/admin/eleves/export/xlsx", headers=h, params=base | {"fields": "nom,classe"})
    assert r.status_code == 200, r.text
    ws = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("Nom", "Classe")
    assert len(rows) - 1 == actifs
