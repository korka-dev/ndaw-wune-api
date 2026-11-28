"""Affectation superviseur → tuteurs depuis l'onglet « Classes » (fichier synthétique)."""
from __future__ import annotations

import io
import uuid

import openpyxl

from app.core.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.services import affectation_superviseurs_service as service

from .conftest import create_user

ENTETE = ["Code Classe NWN", "Code École", "École", "Enseignant proposé", "Téléphone enseignant",
          "Superviseur proposé", "Téléphone superviseur"]


def _tel() -> str:
    return f"77{uuid.uuid4().int % 10**7:07d}"


def _xlsx(lignes: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Classes"
    ws.append(ENTETE)
    for l in lignes:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_affecte_les_tuteurs_par_telephone(tag):
    ts, tc, tt1, tt2, tt3 = _tel(), _tel(), _tel(), _tel(), _tel()
    sup = await create_user(UserRole.superviseur, tag, phone=ts)
    coord = await create_user(UserRole.coordonnateur, tag, phone=tc)          # ancien compte « superviseur »
    t1 = await create_user(UserRole.enseignant, tag, phone=tt1)
    t2 = await create_user(UserRole.enseignant, tag, phone=tt2)
    t3 = await create_user(UserRole.enseignant, tag, phone=tt3)
    contenu = _xlsx([
        ["1", "1", "Ecole A", "T1", f"+221 {tt1[:2]} {tt1[2:5]} {tt1[5:7]} {tt1[7:]}", "Sup", ts],
        ["2", "1", "Ecole A", "T2", tt2, "Sup", ts],
        ["3", "2", "Ecole B", "T3", tt3, "Coord", tc],
        ["4", "3", "Ecole C", "Inconnu", "770000000", "Sup inconnu", "760000000"],
        ["5", "3", "Ecole C", "", "", "", ""],
        ["6", "1", "Ecole A", "T absent", "761111111", "Sup", ts],
    ])
    lignes = service.lire(contenu)
    assert len(lignes) == 6

    async with AsyncSessionLocal() as db:
        r = await service.traiter(db, lignes)
        await db.commit()
    assert {x["superviseur"] for x in r["affectes"]} == {sup.name, coord.name}
    assert [x["telephone"] for x in r["superviseurs_introuvables"]] == ["760000000"]
    assert {x["telephone"] for x in r["tuteurs_introuvables"]} == {"761111111"}
    assert len(r["classes_incompletes"]) == 1

    async with AsyncSessionLocal() as db:
        s = await db.get(User, sup.id)
        c = await db.get(User, coord.id)
        assert set(s.classes) == {str(t1.id), str(t2.id)}
        assert c.classes == [str(t3.id)]

        # Relance : rien ne change.
        r2 = await service.traiter(db, lignes)
        assert not r2["affectes"] and len(r2["inchanges"]) == 2
        await db.rollback()


async def test_ajout_par_defaut_et_remplacement(tag):
    ts, ta, tb = _tel(), _tel(), _tel()
    ancien = await create_user(UserRole.enseignant, tag, phone=ta)
    nouveau = await create_user(UserRole.enseignant, tag, phone=tb)
    sup = await create_user(UserRole.superviseur, tag, phone=ts, classes=[str(ancien.id)])
    lignes = service.lire(_xlsx([["1", "1", "Ecole", "N", tb, "S", ts]]))

    async with AsyncSessionLocal() as db:
        await service.traiter(db, lignes)
        assert (await db.get(User, sup.id)).classes == [str(ancien.id), str(nouveau.id)]
        await db.rollback()
    async with AsyncSessionLocal() as db:
        await service.traiter(db, lignes, remplacer=True)
        assert (await db.get(User, sup.id)).classes == [str(nouveau.id)]
        await db.rollback()


async def test_evaluateur_n_est_pas_un_superviseur(tag):
    te, tt = _tel(), _tel()
    await create_user(UserRole.coordonnateur, tag, phone=te, title="evaluateur")
    await create_user(UserRole.enseignant, tag, phone=tt)
    async with AsyncSessionLocal() as db:
        r = await service.traiter(db, service.lire(_xlsx([["1", "1", "E", "T", tt, "Ev", te]])))
        assert [x["telephone"] for x in r["superviseurs_introuvables"]] == [te]
        await db.rollback()
