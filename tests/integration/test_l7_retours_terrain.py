"""Lot L7 — retours des tuteurs et superviseurs (enquêtes NWV 2026, RET01-RET15)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.terrain import ContactApp, EvaluationAbsence
from app.models.user import UserRole

from .conftest import bearer, create_eleve, create_school, create_user, login

API = "/api/v1"
TODAY = date.today()


async def _acteurs(client, tag):
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    titulaire = await create_eleve(school, "CE1", tag, "Titulaire")
    remplacant = await create_eleve(school, "CE1", tag, "Remplaçant")
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id, classes=[str(tuteur.id)])
    autre_sup = await create_user(UserRole.superviseur, f"{tag}x")
    admin = await create_user(UserRole.admin, tag)
    return {"school": school, "tuteur": tuteur, "titulaire": titulaire, "remplacant": remplacant,
            "ht": bearer(await login(client, tuteur)), "hs": bearer(await login(client, sup)),
            "hx": bearer(await login(client, autre_sup)), "ha": bearer(await login(client, admin))}


# ── RET03 : remplacements selon la règle fixée par ARED ────────────────────

async def test_remplacement_immediat_par_defaut(client, tag):
    t = await _acteurs(client, tag)
    r = await client.post(f"{API}/app/remplacements", headers=t["ht"], json={
        "ancien_eleve_id": str(t["titulaire"].id), "nouveau_eleve_id": str(t["remplacant"].id), "motif": "Déménagement"})
    assert r.status_code == 201, r.text
    assert r.json()["statut"] == "applique"
    h = await client.get(f"{API}/app/remplacements", headers=t["ht"])
    assert h.status_code == 200 and h.json()[0]["motif"] == "Déménagement"


async def test_remplacement_a_valider_par_le_superviseur(client, tag, monkeypatch):
    monkeypatch.setattr(settings, "REMPLACEMENT_VALIDATION", "superviseur")
    t = await _acteurs(client, tag)
    corps = {"ancien_eleve_id": str(t["titulaire"].id), "nouveau_eleve_id": str(t["remplacant"].id), "motif": "Abandon"}
    r = await client.post(f"{API}/app/remplacements", headers=t["ht"], json=corps)
    assert r.status_code == 201 and r.json()["statut"] == "en_attente"
    async with AsyncSessionLocal() as s:
        assert (await s.get(Eleve, t["titulaire"].id)).statut == "actif"   # rien n'a changé
    assert (await client.post(f"{API}/app/remplacements", headers=t["ht"], json=corps)).status_code == 409

    rid = r.json()["id"]
    # Un superviseur qui ne suit pas ce tuteur ne voit ni ne décide rien.
    assert (await client.get(f"{API}/app/supervisor/remplacements", headers=t["hx"])).json() == []
    assert (await client.post(f"{API}/app/supervisor/remplacements/{rid}/decision", headers=t["hx"],
                              json={"decision": "valider"})).status_code == 404
    # Refus sans motif interdit, puis validation par le superviseur affecté.
    assert (await client.post(f"{API}/app/supervisor/remplacements/{rid}/decision", headers=t["hs"],
                              json={"decision": "refuser"})).status_code == 422
    v = await client.post(f"{API}/app/supervisor/remplacements/{rid}/decision", headers=t["hs"], json={"decision": "valider"})
    assert v.status_code == 200 and v.json()["statut"] == "applique"
    async with AsyncSessionLocal() as s:
        assert (await s.get(Eleve, t["titulaire"].id)).statut == "inactif"     # historique conservé (pas supprimé)
        assert (await s.get(Eleve, t["remplacant"].id)).statut_selection == "Titulaire"
    assert (await client.post(f"{API}/app/supervisor/remplacements/{rid}/decision", headers=t["hs"],
                              json={"decision": "valider"})).status_code == 409


# ── RET05 : rapports partagés et retour du superviseur ─────────────────────

async def test_retour_superviseur_visible_par_le_tuteur(client, tag):
    t = await _acteurs(client, tag)
    r = await client.post(f"{API}/app/rapports/journalier", headers=t["ht"], json={
        "date_rapport": TODAY.isoformat(), "ief": "I", "commune": "C", "ecole": "E", "superviseur": "S",
        "nom_tuteur": "T", "nb_absences": 0, "semaine": 1, "jour_cours": 1, "difficultes": '["Aucune"]',
        "directeur_venu": False, "besoin_appui": False})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    liste = await client.get(f"{API}/app/supervisor/rapports-tuteurs", headers=t["hs"])
    assert [x["id"] for x in liste.json()] == [rid]
    assert (await client.get(f"{API}/app/supervisor/rapports-tuteurs", headers=t["hx"])).json() == []
    assert (await client.put(f"{API}/app/supervisor/rapports-tuteurs/{rid}/retour", headers=t["hx"],
                             json={"texte": "Bien"})).status_code == 404
    ret = await client.put(f"{API}/app/supervisor/rapports-tuteurs/{rid}/retour", headers=t["hs"],
                           json={"texte": "Bonne séance, pensez au guide."})
    assert ret.status_code == 200 and ret.json()["retour_par"]
    mes = await client.get(f"{API}/app/rapports/journalier", headers=t["ht"])
    item = mes.json()["items"][0]
    assert item["retour_superviseur"] == "Bonne séance, pensez au guide." and item["retour_le"]


# ── RET15/RET13 : trace de synchronisation et fraîcheur ────────────────────

async def test_sync_trace_version_et_parametres(client, tag, monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_RAPPORT_OBLIGATOIRE", False)
    t = await _acteurs(client, tag)
    r = await client.get(f"{API}/app/sync", headers={**t["ht"], "X-App-Version": "1.3.0"})
    assert r.status_code == 200, r.text
    p = r.json()["parametres_terrain"]
    assert p["photo_rapport_obligatoire"] is False and p["remplacement_validation"] == "immediat"
    async with AsyncSessionLocal() as s:
        c = (await s.execute(select(ContactApp).where(ContactApp.user_id == t["tuteur"].id))).scalar_one()
        assert c.version_app == "1.3.0" and c.minuteur_expose is True
    act = await client.get(f"{API}/app/supervisor/tuteurs-activite", headers=t["hs"])
    assert act.status_code == 200 and act.json()[0]["fraicheur"] == "à jour"
    assert act.json()[0]["version_app"] == "1.3.0"


# ── RET04 : élève absent au tirage ──────────────────────────────────────────

async def test_absence_evaluation_sans_substitution(client, tag):
    t = await _acteurs(client, tag)
    corps = {"evaluations": [], "absents": [{"eleve_id": str(t["titulaire"].id), "date_eval": TODAY.isoformat()}]}
    r = await client.post(f"{API}/app/supervisor/evaluations", headers=t["hs"], json=corps)
    assert r.status_code == 201, r.text
    assert r.json()["absents_enregistres"] == 1
    r2 = await client.post(f"{API}/app/supervisor/evaluations", headers=t["hs"], json=corps)   # rejeu
    assert r2.status_code == 201 and r2.json()["absents_enregistres"] == 0
    async with AsyncSessionLocal() as s:
        n = (await s.execute(select(EvaluationAbsence).where(EvaluationAbsence.eleve_id == t["titulaire"].id))).scalars().all()
        assert len(n) == 1
    # Absent et évalué le même jour : lot refusé.
    mixte = {"evaluations": [{"eleve_id": str(t["titulaire"].id), "competence": "lettres", "resultat": "reussi",
                              "date_eval": TODAY.isoformat()}], "absents": corps["absents"]}
    assert (await client.post(f"{API}/app/supervisor/evaluations", headers=t["hs"], json=mixte)).status_code == 422


# ── RET07 : proposition de changement de groupe ────────────────────────────

async def test_proposition_groupe_conserve_l_ancien(client, tag):
    t = await _acteurs(client, tag)
    async with AsyncSessionLocal() as s:
        (await s.get(Eleve, t["titulaire"].id)).groupe_lecture = "G1"
        await s.commit()
    r = await client.post(f"{API}/app/propositions-groupe", headers=t["ht"], json={
        "eleve_id": str(t["titulaire"].id), "domaine": "lecture", "nouveau_groupe": "G2", "motif": "Lit des mots"})
    assert r.status_code == 201, r.text
    assert r.json()["ancien_groupe"] == "G1" and r.json()["statut"] == "proposee"
    pid = r.json()["id"]
    d = await client.post(f"{API}/admin/propositions-groupe/{pid}/decision", headers=t["ha"], json={"decision": "valider"})
    assert d.status_code == 200 and d.json()["statut"] == "validee" and d.json()["ancien_groupe"] == "G1"
    async with AsyncSessionLocal() as s:
        assert (await s.get(Eleve, t["titulaire"].id)).groupe_lecture == "G2"
    # Élève hors des classes du tuteur : 404.
    ailleurs = await create_eleve(await create_school(f"{tag}b"), "CE1", tag, "Titulaire")
    r = await client.post(f"{API}/app/propositions-groupe", headers=t["ht"], json={
        "eleve_id": str(ailleurs.id), "domaine": "maths", "nouveau_groupe": "M1", "motif": "Test"})
    assert r.status_code == 404


# ── RET13 : message au superviseur ─────────────────────────────────────────

async def test_message_au_superviseur(client, tag):
    t = await _acteurs(client, tag)
    m = await client.post(f"{API}/app/remarques", headers=t["ht"], json={
        "categorie": "pedagogie", "message": "Comment gérer deux groupes ?", "destinataire": "superviseur",
        "contexte_type": "classe", "contexte_id": "CE1"})
    assert m.status_code == 201, m.text
    recus = await client.get(f"{API}/app/supervisor/messages", headers=t["hs"])
    assert [x["message"] for x in recus.json()] == ["Comment gérer deux groupes ?"]
    assert (await client.get(f"{API}/app/supervisor/messages", headers=t["hx"])).json() == []
    rep = await client.post(f"{API}/app/supervisor/messages/{m.json()['id']}/reponse", headers=t["hs"],
                            json={"texte": "Alternez les activités."})
    assert rep.status_code == 200
    mes = await client.get(f"{API}/app/remarques", headers=t["ht"])
    assert mes.json()[0]["reponse"] == "Alternez les activités." and mes.json()[0]["reponse_par_nom"]
