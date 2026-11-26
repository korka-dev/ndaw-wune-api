"""Lot L10 — comptes inspecteurs importés depuis l'onglet « Points_focaux »."""
from __future__ import annotations

import io
import json
import uuid

import openpyxl

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.school import School
from app.models.user import UserRole

from .conftest import bearer, create_user, login

API = "/api/v1"
URL = f"{API}/admin/inspecteurs/import-points-focaux"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ENTETE = ["ID point focal", "Nom repris", "Téléphone national", "Niveau structure", "Structure indiquée",
          "IEF harmonisée", "Fonction indiquée", "IEF proposée", "Observation"]


def _tel() -> str:
    return f"77{uuid.uuid4().int % 10**7:07d}"


def _fichier(lignes) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.title = "Base_application"
    ws = wb.create_sheet("Points_focaux")
    ws.append(ENTETE)
    for li in lignes:
        ws.append(li)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import_des_points_focaux(client, tag):
    t = tag.upper()
    ief_a, ief_b = f"IEF Alpha {t}", f"IEF Beta {t}"
    async with AsyncSessionLocal() as s:
        s.add_all([School(name=f"EA {t}", region=ief_a), School(name=f"EB {t}", region=ief_b)])
        await s.commit()
    sup = await create_user(UserRole.superviseur, tag, phone=_tel())
    tel_a, tel_b = _tel(), _tel()
    contenu = _fichier([
        ["PF-001", f"INSP IA {t}", _tel(), "IA", "IA Podor", None, "Inspecteur", None, "À confirmer"],
        ["PF-002", f"INSP A {t}", tel_a, "IEF", "Alpha", ief_a.lower(), "Inspecteur", None, None],
        ["PF-003", f"INSP B {t}", tel_b, "IEF", "Beta", None, "Inspecteur", ief_b, "À confirmer"],
        ["PF-004", f"INSP SUP {t}", sup.phone, "IEF", "Alpha", ief_a, None, None, None],
        ["PF-005", f"INSP X {t}", _tel(), "IEF", "Inconnue", f"IEF Inconnue {t}", None, None, None],
    ])
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))

    def envoyer(params, data=None):
        return client.post(URL, headers=h, params=params, data=data or {},
                           files={"file": ("base.xlsx", contenu, XLSX)})

    apercu = (await envoyer({})).json()
    assert apercu["dry_run"] is True and apercu["lignes"] == 5
    assert [x["pf"] for x in apercu["crees"]] == ["PF-002"] and apercu["crees"][0]["ief"] == ief_a
    assert {x["pf"]: x["ief_proposee"] for x in apercu["a_rattacher"]} == {"PF-001": None, "PF-003": ief_b}
    assert {x["pf"] for x in apercu["ignores"]} == {"PF-004", "PF-005"}  # numéro d'un superviseur, IEF sans école

    r = (await envoyer({"dry_run": "false"}, {"rattachements": json.dumps({"PF-003": ief_b}),
                                              "exclus": json.dumps(["PF-001"])})).json()
    assert {x["pf"] for x in r["crees"]} == {"PF-002", "PF-003"} and r["a_rattacher"] == []
    login_a = await client.post(f"{API}/auth/login", json={"identifier": tel_a, "password": settings.DEFAULT_USER_PASSWORD})
    assert login_a.status_code == 200 and login_a.json()["must_change_password"] is True

    liste = (await client.get(f"{API}/admin/inspecteurs", headers=h, params={"limit": 500})).json()["items"]
    assert {u["phone"] for u in liste} >= {tel_a, tel_b}

    r2 = (await envoyer({"dry_run": "false"}, {"rattachements": json.dumps({"PF-003": ief_b})})).json()
    assert r2["crees"] == [] and {x["pf"] for x in r2["inchanges"]} == {"PF-002", "PF-003"}


async def test_fichier_sans_onglet_points_focaux(client, tag):
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))
    r = await client.post(URL, headers=h, files={"file": ("x.xlsx", buf.getvalue(), XLSX)})
    assert r.status_code == 422 and "Points_focaux" in r.json()["detail"]
