"""Lot L11 — deux emplois du temps (écoles avec / sans FLS) importés depuis Word."""
from __future__ import annotations

import io
from datetime import date

import docx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.planning import PlanningSegment
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.school import School
from app.models.user import UserRole

from .conftest import bearer, create_user, login

API = "/api/v1"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_sans_fls() -> bytes:
    """Mise en page du fichier « Emploi du temps » : un titre de jour puis un tableau Heure | Activité."""
    d = docx.Document()
    d.add_paragraph("Emploi du temps NWAS 2026-2027")
    for jour, fin in (("LUNDI", "Lecture indépendante (15 mn)"), ("MERCREDI", "Lecture indépendante (15 mn)"),
                      ("VENDREDI", "Jeu éducatif (15 mn)")):
        d.add_paragraph(jour)
        t = d.add_table(rows=1, cols=2)
        t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Heure", "Activité"
        for h, a in (("16h00-16h05", "Promesse (5 mn)"), ("16h05-16h40", "Leçon Maths (35 mn)"),
                     ("17h45-18h00", fin)):
            r = t.add_row().cells
            r[0].text, r[1].text = h, a
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _docx_fls() -> bytes:
    """Mise en page du fichier FLS : un tableau Horaire | durée | Lundi | Mercredi | Vendredi, cellules fusionnées."""
    d = docx.Document()
    t = d.add_table(rows=1, cols=5)
    for c, v in zip(t.rows[0].cells, ("Horaire", "durée", "Lundi", "Mercredi", "Vendredi")):
        c.text = v
    for h, duree, a in (("16 h 00 à 16 h 05", "5 mn", "Accueil et promesse"),
                        ("16 h 05 - 16 h 15", "10 mn", "Exploitation du cahier de récits"),
                        ("17 h 20 - 17 h 50", "30 mn", "Leçon de français langue seconde (FLS)")):
        r = t.add_row().cells
        r[0].text, r[1].text = h, duree
        r[2].merge(r[4]).text = a
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def _campagne(tag: str) -> ProgramSession:
    async with AsyncSessionLocal() as s:
        ps = ProgramSession(name=f"Campagne L11 {tag}", date_debut=date(2026, 1, 1), date_fin=date(2027, 12, 31),
                            status=SessionStatus.active)
        s.add(ps)
        await s.commit()
        await s.refresh(ps)
        return ps


async def _importer(client, ha, camp, type_ecole, contenu, dry_run=False):
    return await client.post(f"{API}/admin/planning/import", headers=ha,
                             params={"session_id": str(camp.id), "type_ecole": type_ecole, "dry_run": dry_run},
                             files={"file": ("edt.docx", contenu, DOCX)})


async def _sync(client, tag, camp, fls):
    async with AsyncSessionLocal() as s:
        e = School(name=f"Ecole L11 {tag} {fls}", fls=fls)
        s.add(e)
        await s.commit()
        await s.refresh(e)
    u = await create_user(UserRole.enseignant, f"{tag}{fls}", school_id=e.id, classes=["CI"])
    async with AsyncSessionLocal() as s:
        s.add(TeacherSession(teacher_id=u.id, session_id=camp.id))
        await s.commit()
    h = bearer(await login(client, u))
    return (await client.get(f"{API}/app/sync", headers=h)).json()["planning"]


async def test_deux_emplois_du_temps_selon_le_type_d_ecole(client, tag):
    camp = await _campagne(tag)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))

    apercu = await _importer(client, ha, camp, "sans_fls", _docx_sans_fls(), dry_run=True)
    assert apercu.status_code == 200, apercu.text
    assert "écoles sans FLS" in apercu.json()["perimetre"]

    r = await _importer(client, ha, camp, "sans_fls", _docx_sans_fls())
    assert r.status_code == 200, r.text
    # Même heure de début (16:00) dans les deux : pas de conflit d'unicité.
    r = await _importer(client, ha, camp, "fls", _docx_fls())
    assert r.status_code == 200, r.text

    async with AsyncSessionLocal() as s:
        segs = (await s.execute(select(PlanningSegment).where(PlanningSegment.session_id == camp.id))).scalars().all()
    par_type = {}
    for x in segs:
        par_type.setdefault(x.type_ecole, []).append(x)
    assert (len(par_type["sans_fls"]), len(par_type["fls"])) == (9, 9)
    assert all(x.reserve_fls for x in par_type["fls"]) and not any(x.reserve_fls for x in par_type["sans_fls"])
    assert {x.matiere for x in par_type["sans_fls"]} == {"Promesse", "Leçon Maths", "Lecture indépendante", "Jeu éducatif"}
    vendredi = sorted((x.heure_debut.strftime("%H:%M"), x.matiere) for x in par_type["sans_fls"] if x.jour == 4)
    assert vendredi[-1] == ("17:45", "Jeu éducatif")
    assert {x.jour for x in par_type["fls"]} == {0, 2, 4}

    avec = await _sync(client, tag, camp, True)
    sans = await _sync(client, tag, camp, False)
    inconnu = await _sync(client, tag, camp, None)
    assert {p["type_ecole"] for p in avec} == {"fls"} and len(avec) == 9
    assert "Leçon de français langue seconde (FLS)" in {p["matiere"] for p in avec}
    assert {p["type_ecole"] for p in sans} == {"sans_fls"} and len(sans) == 9
    assert [p["matiere"] for p in inconnu] == [p["matiere"] for p in sans]

    # Réimporter l'emploi du temps sans FLS ne touche pas celui des écoles FLS.
    r = await _importer(client, ha, camp, "sans_fls", _docx_sans_fls())
    assert r.status_code == 200
    resume = (await client.get(f"{API}/admin/planning/fls/resume", headers=ha,
                               params={"session_id": str(camp.id)})).json()
    assert resume["creneaux"] == {"tous": 0, "fls": 9, "sans_fls": 9}

    liste = (await client.get(f"{API}/admin/planning", headers=ha,
                              params={"session_id": str(camp.id), "type_ecole": "fls", "page_size": 100})).json()
    assert liste["total"] == 9 and {x["type_ecole"] for x in liste["items"]} == {"fls"}


async def test_creneau_cree_a_la_main_avec_un_type(client, tag):
    camp = await _campagne(tag)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    url = f"{API}/admin/planning"
    r = await client.post(url, headers=ha, json={"session_id": str(camp.id), "jour": 0, "heure_debut": "16:00",
                                                 "heure_fin": "16:05", "matiere": "Promesse", "type_ecole": "sans_fls"})
    assert r.status_code == 201 and (r.json()["type_ecole"], r.json()["reserve_fls"]) == ("sans_fls", False)
    r = await client.patch(f"{url}/{r.json()['id']}", headers=ha, json={"type_ecole": "fls"})
    assert (r.json()["type_ecole"], r.json()["reserve_fls"]) == ("fls", True)
    r = await client.patch(f"{url}/{r.json()['id']}", headers=ha, json={"reserve_fls": False})
    assert r.json()["type_ecole"] == "tous"
    r = await client.post(url, headers=ha, json={"session_id": str(camp.id), "jour": 0, "heure_debut": "16:00",
                                                 "heure_fin": "16:05", "matiere": "X", "type_ecole": "autre"})
    assert r.status_code == 422


async def test_document_word_illisible(client, tag):
    camp = await _campagne(tag)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    r = await _importer(client, ha, camp, "fls", b"pas un docx")
    assert r.status_code == 400
