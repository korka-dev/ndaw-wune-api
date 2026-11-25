"""Lot L9 — écoles avec / sans FLS : visibilité de l'emploi du temps et import de la répartition."""
from __future__ import annotations

import io
import uuid
from datetime import date, time

import openpyxl

from app.core.database import AsyncSessionLocal
from app.models.planning import PlanningSegment
from app.models.school import School
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import UserRole
from app.services.fls_service import est_creneau_fls

from .conftest import bearer, create_user, login

API = "/api/v1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _ecole(nom: str, fls=None, region=None) -> School:
    async with AsyncSessionLocal() as s:
        e = School(name=nom, fls=fls, region=region)
        s.add(e)
        await s.commit()
        await s.refresh(e)
        return e


async def _campagne_avec_planning(tag: str) -> ProgramSession:
    async with AsyncSessionLocal() as s:
        ps = ProgramSession(name=f"Campagne FLS {tag}", date_debut=date(2026, 1, 1), date_fin=date(2027, 12, 31),
                            status=SessionStatus.active)
        s.add(ps)
        await s.flush()
        for debut, fin, matiere in [(time(10), time(10, 40), "Leçon de lecture"),
                                    (time(11, 20), time(11, 50), "Français langue seconde"),
                                    (time(11, 50), time(12, 10), "Lecture indépendante")]:
            s.add(PlanningSegment(session_id=ps.id, jour=0, heure_debut=debut, heure_fin=fin, matiere=matiere,
                                  reserve_fls=est_creneau_fls(matiere)))
        await s.commit()
        await s.refresh(ps)
        return ps


async def _tuteur(client, tag, ecole, campagne):
    u = await create_user(UserRole.enseignant, tag, school_id=ecole.id, classes=["CI"])
    async with AsyncSessionLocal() as s:
        s.add(TeacherSession(teacher_id=u.id, session_id=campagne.id))
        await s.commit()
    return bearer(await login(client, u))


def test_detection_des_creneaux_fls():
    assert est_creneau_fls("Français langue seconde") and est_creneau_fls("FRANCAIS LANGUE SECONDE")
    assert est_creneau_fls("Leçon de FLS") and est_creneau_fls("fls")
    assert not est_creneau_fls("Leçon de lecture") and not est_creneau_fls("Flûte") and not est_creneau_fls(None)


async def test_tuteur_ecole_sans_fls_ne_voit_pas_le_creneau_fls(client, tag):
    camp = await _campagne_avec_planning(tag)
    matieres = {}
    for cas, fls in (("avec", True), ("sans", False), ("inconnu", None)):
        h = await _tuteur(client, f"{tag}{cas}", await _ecole(f"Ecole {cas} {tag}", fls=fls), camp)
        sync = (await client.get(f"{API}/app/sync", headers=h)).json()
        assert sync["active_session"]["id"] == str(camp.id)
        matieres[cas] = [p["matiere"] for p in sync["planning"]]
    assert "Français langue seconde" in matieres["avec"]
    assert "Français langue seconde" in matieres["inconnu"]  # non renseigné : rien ne change
    assert matieres["sans"] == ["Leçon de lecture", "Lecture indépendante"]


async def test_apercu_admin_par_type_d_ecole_et_detection_a_la_creation(client, tag):
    camp = await _campagne_avec_planning(tag)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    url = f"{API}/admin/planning"
    tous = (await client.get(url, headers=ha, params={"session_id": str(camp.id)})).json()
    sans = (await client.get(url, headers=ha, params={"session_id": str(camp.id), "type_ecole": "sans_fls"})).json()
    assert (tous["total"], sans["total"]) == (3, 2)
    assert [x["reserve_fls"] for x in tous["items"]] == [False, True, False]
    assert (await client.get(url, headers=ha, params={"type_ecole": "x"})).status_code == 422

    r = await client.post(url, headers=ha, json={"session_id": str(camp.id), "jour": 1, "heure_debut": "11:20",
                                                 "heure_fin": "11:50", "matiere": "FLS"})
    assert r.status_code == 201 and r.json()["reserve_fls"] is True
    # Décochable à la main, sans changer la matière.
    r = await client.patch(f"{url}/{r.json()['id']}", headers=ha, json={"reserve_fls": False})
    assert r.json()["reserve_fls"] is False


def _fichier(lignes_recap, feuilles_ief=()) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Répartition écoles"
    entete = ["IA", "IEF", "Écoles avec FLS", "Écoles sans FLS"]
    ws.append(entete)
    for ligne in lignes_recap:
        ws.append(ligne)
    for titre, lignes in feuilles_ief:
        f = wb.create_sheet(titre)
        f.append(entete)
        for ligne in lignes:
            f.append(ligne)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import_de_la_repartition_fls(client, tag):
    t = tag.upper()
    a = await _ecole(f"NDECK {t}")                                   # nom exact
    b = await _ecole(f"EE Ngoye {t}", region=f"IEF Bambey {t}")       # casse
    c = await _ecole(f"Bambey Sérère 3 {t}")                          # accents, IEF absente
    d1 = await _ecole(f"Batal {t}", region=f"Diourbel {t}")           # même nom dans 2 IEF
    d2 = await _ecole(f"Batal {t}", region=f"Bambey {t}")
    await _ecole(f"Sobeme Sessene {t}")                               # proche de « SOBEME SESENE »
    ief = f"IEF Bambey {t}"
    recap = [
        ["IA Diourbel", ief, f"NDECK {t}", None],
        ["IA Diourbel", ief, None, f"EE NGOYE {t}"],
        ["IA Diourbel", ief, f"BAMBEY SERERE 3 {t}", None],
        ["IA Diourbel", ief, f"BATAL {t}", None],
        ["IA Diourbel", ief, None, f"SOBEME SESENE {t}"],
        ["IA Diourbel", ief, f"INCONNUE {t}", None],
    ]
    contenu = _fichier(recap, [("Bambey", recap[:2])])  # doublon recap / feuille IEF : lu une fois
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    url = f"{API}/admin/planning/import-fls"

    def envoyer(params):
        return client.post(url, headers=ha, params=params, files={"file": ("repartition.xlsx", contenu, XLSX)})

    apercu = (await envoyer({})).json()
    assert apercu["dry_run"] is True and apercu["lignes_fichier"] == 6
    assert {x["nom"] for x in apercu["trouvees"]} == {a.name, b.name, c.name, d2.name}
    assert next(x for x in apercu["trouvees"] if x["nom"] == d2.name)["id"] == str(d2.id)  # départagée par l'IEF
    introuvables = {x["nom_fichier"]: x["suggestion"] for x in apercu["introuvables"]}
    assert introuvables == {f"SOBEME SESENE {t}": f"Sobeme Sessene {t}", f"INCONNUE {t}": None}
    async with AsyncSessionLocal() as s:
        assert (await s.get(School, a.id)).fls is None  # aperçu : rien n'est modifié

    r = (await envoyer({"dry_run": "false", "completer_ief": "true"})).json()
    assert r["a_modifier"] == 4 and r["ief_completees"] == 2  # b et d2 ont déjà leur IEF
    async with AsyncSessionLocal() as s:
        etats = {e.name: (e.fls, e.region) for e in [await s.get(School, x.id) for x in (a, b, c, d1, d2)]}
    assert etats[a.name] == (True, ief) and etats[b.name] == (False, ief) and etats[c.name] == (True, ief)
    assert etats[d2.name][0] is True and (await _fls(d1.id)) is None

    # Le même fichier dans l'import d'emploi du temps : refusé avec un message clair.
    r = await client.post(f"{API}/admin/planning/import", headers=ha, params={"session_id": str(uuid.uuid4())},
                          files={"file": ("repartition.xlsx", contenu, XLSX)})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "FICHIER_REPARTITION_FLS"

    resume = (await client.get(f"{API}/admin/planning/fls/resume", headers=ha)).json()
    assert resume["avec_fls"] >= 3 and resume["sans_fls"] >= 1


async def _fls(ecole_id):
    async with AsyncSessionLocal() as s:
        return (await s.get(School, ecole_id)).fls


async def test_import_refuse_un_fichier_qui_n_est_pas_la_repartition(client, tag):
    wb = openpyxl.Workbook()
    wb.active.append(["jour", "heure_debut", "heure_fin", "matiere"])
    buf = io.BytesIO()
    wb.save(buf)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    r = await client.post(f"{API}/admin/planning/import-fls", headers=ha,
                          files={"file": ("planning.xlsx", buf.getvalue(), XLSX)})
    assert r.status_code == 422
