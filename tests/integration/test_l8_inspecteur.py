"""Lot L8 — inspecteur (IEF) : comptes créés par l'admin, consultation en lecture seule."""
from __future__ import annotations

import uuid

from app.core.database import AsyncSessionLocal
from app.models.school import School
from app.models.user import UserRole

from .conftest import bearer, create_user, login
from .test_l3_integrite import _rj

API = "/api/v1"


async def _ecole(tag: str, ief: str) -> School:
    async with AsyncSessionLocal() as s:
        sc = School(name=f"Ecole L8 {tag} {uuid.uuid4().hex[:4]}", region=ief, city="Commune L8")
        s.add(sc)
        await s.commit()
        await s.refresh(sc)
        return sc


async def _rapport(client, tag, ecole: School, **over) -> str:
    tuteur = await create_user(UserRole.enseignant, tag, school_id=ecole.id)
    r = await client.post(f"{API}/app/rapports/journalier", headers=bearer(await login(client, tuteur)),
                          json=_rj(tag, ecole=ecole.name, **over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_admin_cree_un_inspecteur_rattache_a_une_ief_existante(client, tag):
    ief = f"IEF Créa {tag}"
    await _ecole(tag, ief)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    url = f"{API}/admin/inspecteurs"

    iefs = (await client.get(f"{url}/iefs", headers=ha)).json()
    assert {"ief": ief, "nb_ecoles": 1} in iefs

    # IEF inconnue, IEF absente, téléphone absent : refusés.
    base = {"name": "Insp", "phone": f"77{uuid.uuid4().int % 10**7:07d}", "role": "inspecteur"}
    assert (await client.post(url, headers=ha, json={**base, "ief": "IEF qui n'existe pas"})).status_code == 422
    assert (await client.post(url, headers=ha, json=base)).status_code == 422
    assert (await client.post(url, headers=ha, json={**base, "phone": None, "email": f"i{tag}@test.invalid",
                                                       "ief": ief})).status_code == 422

    # Casse et espaces ignorés ; le libellé enregistré est celui des écoles.
    r = await client.post(url, headers=ha, json={**base, "ief": f"  {ief.upper()} ", "role": "admin"})
    assert r.status_code == 201, r.text
    cree = r.json()
    assert (cree["role"], cree["ief"], cree["must_change_password"]) == ("inspecteur", ief, True)
    assert any(u["id"] == cree["id"] for u in (await client.get(url, headers=ha)).json()["items"])

    # Champs propres aux tuteurs refusés en modification.
    r = await client.patch(f"{url}/{cree['id']}", headers=ha, json={"classes": ["CI"]})
    assert r.status_code == 422

    # Email et fonction peuvent être effacés ; le téléphone, non.
    r = await client.patch(f"{url}/{cree['id']}", headers=ha,
                           json={"email": f"insp.{tag}@test.invalid", "title": "IEF adjoint"})
    assert (r.json()["email"], r.json()["title"]) == (f"insp.{tag}@test.invalid", "IEF adjoint")
    r = await client.patch(f"{url}/{cree['id']}", headers=ha, json={"email": "", "title": None})
    assert r.status_code == 200 and (r.json()["email"], r.json()["title"]) == (None, None)
    assert (await client.patch(f"{url}/{cree['id']}", headers=ha, json={"phone": ""})).status_code == 422


async def test_inspecteur_voit_seulement_les_ecoles_et_rapports_de_son_ief(client, tag):
    ief, autre = f"IEF A {tag}", f"IEF B {tag}"
    e1, e2, hors = await _ecole(tag, ief), await _ecole(tag, ief.lower()), await _ecole(tag, autre)
    r1 = await _rapport(client, tag, e1, besoin_appui=True, domaines_appui='["lecture"]')
    r2 = await _rapport(client, tag, e2)
    r_hors = await _rapport(client, tag, hors)

    insp = await create_user(UserRole.inspecteur, tag, ief=ief)
    h = bearer(await login(client, insp))
    base = f"{API}/app/inspecteur"

    me = (await client.get(f"{API}/auth/me", headers=h)).json()
    assert (me["role"], me["ief"]) == ("inspecteur", ief)

    ecoles = (await client.get(f"{base}/ecoles", headers=h)).json()
    assert {e["id"] for e in ecoles} == {str(e1.id), str(e2.id)}
    assert next(e for e in ecoles if e["id"] == str(e1.id))["besoins_appui_7_jours"] == 1

    resume = (await client.get(f"{base}/resume", headers=h)).json()
    assert (resume["nb_ecoles"], resume["nb_tuteurs"], resume["rapports_7_jours"]) == (2, 2, 2)

    page = (await client.get(f"{base}/rapports", headers=h)).json()
    assert {x["id"] for x in page["items"]} == {r1, r2} and page["total"] == 2
    filtre = (await client.get(f"{base}/rapports", headers=h, params={"ecole_id": str(e1.id)})).json()
    assert [x["id"] for x in filtre["items"]] == [r1]
    assert [x["id"] for x in (await client.get(f"{base}/rapports", headers=h,
                                                params={"besoin_appui": "true"})).json()["items"]] == [r1]

    assert (await client.get(f"{base}/rapports/{r1}", headers=h)).status_code == 200
    # Hors périmètre : 404, comme si le rapport ou l'école n'existait pas.
    assert (await client.get(f"{base}/rapports/{r_hors}", headers=h)).status_code == 404
    assert (await client.get(f"{base}/rapports/{r_hors}/photo/0", headers=h)).status_code == 404
    assert (await client.get(f"{base}/rapports", headers=h, params={"ecole_id": str(hors.id)})).status_code == 404
    assert (await client.get(f"{base}/ecoles/{hors.id}/personnes", headers=h)).status_code == 404

    personnes = (await client.get(f"{base}/ecoles/{e1.id}/personnes", headers=h)).json()
    assert [(p["role"], p["rapports_7_jours"]) for p in personnes] == [("tuteur", 1)]


async def test_inspecteur_en_lecture_seule_et_hors_des_autres_espaces(client, tag):
    ecole = await _ecole(tag, f"IEF RO {tag}")
    rid = await _rapport(client, tag, ecole)
    h = bearer(await login(client, await create_user(UserRole.inspecteur, tag, ief=f"IEF RO {tag}")))

    # Aucune écriture sur les rapports, ni accès aux espaces tuteur, superviseur ou admin.
    assert (await client.post(f"{API}/app/rapports/journalier", headers=h, json=_rj(tag))).status_code == 403
    assert (await client.put(f"{API}/app/rapports/journalier/{rid}", headers=h,
                             json={"motif": "x", "nb_absences": 1})).status_code == 403
    for chemin in ("/app/sync", "/app/supervisor/sync", "/admin/schools", "/admin/inspecteurs"):
        assert (await client.get(f"{API}{chemin}", headers=h)).status_code == 403, chemin

    # Les autres rôles n'ont pas accès à l'espace inspecteur.
    ht = bearer(await login(client, await create_user(UserRole.enseignant, tag)))
    assert (await client.get(f"{API}/app/inspecteur/rapports", headers=ht)).status_code == 403


async def test_noms_des_absents_masques_sauf_decision_ared(client, tag, monkeypatch):
    from app.core.config import settings
    ecole = await _ecole(tag, f"IEF Abs {tag}")
    rid = await _rapport(client, tag, ecole, nb_absences=2, absents="Awa Diop, Moussa Fall")
    h = bearer(await login(client, await create_user(UserRole.inspecteur, tag, ief=f"IEF Abs {tag}")))
    url = f"{API}/app/inspecteur/rapports/{rid}"

    r = (await client.get(url, headers=h)).json()
    assert (r["nb_absences"], r["absents"]) == (2, None)
    liste = (await client.get(f"{API}/app/inspecteur/rapports", headers=h)).json()["items"]
    assert all(x["absents"] is None for x in liste)
    assert (await client.get(f"{API}/app/inspecteur/resume", headers=h)).json()["noms_absents_visibles"] is False

    monkeypatch.setattr(settings, "INSPECTEUR_NOMS_ABSENTS", True)
    assert (await client.get(url, headers=h)).json()["absents"] == "Awa Diop, Moussa Fall"


async def test_inspecteur_peut_signaler_un_probleme(client, tag):
    h = bearer(await login(client, await create_user(UserRole.inspecteur, tag, ief=f"IEF Sig {tag}")))
    r = await client.post(f"{API}/app/remarques", headers=h,
                          json={"categorie": "application", "message": "Écran vide", "destinataire": "support_technique"})
    assert r.status_code == 201, r.text
    assert r.json()["ecole"] == f"IEF IEF Sig {tag}"
    assert len((await client.get(f"{API}/app/remarques", headers=h)).json()) == 1
    # Écrire « à son superviseur » reste réservé aux tuteurs.
    r = await client.post(f"{API}/app/remarques", headers=h,
                          json={"categorie": "autre", "message": "x", "destinataire": "superviseur"})
    assert r.status_code == 422


async def test_inspecteur_sans_ief_ne_voit_rien(client, tag):
    await _rapport(client, tag, await _ecole(tag, "IEF"))  # ief saisi « IEF » dans _rj
    h = bearer(await login(client, await create_user(UserRole.inspecteur, tag)))
    assert (await client.get(f"{API}/app/inspecteur/ecoles", headers=h)).json() == []
    assert (await client.get(f"{API}/app/inspecteur/rapports", headers=h)).json()["total"] == 0
