"""Lot L6 — modules terrain et données (rapport V2, Partie II D8-D10, APP-33).

Chaque test crée sa campagne fictive, son école et ses acteurs.
"""
from __future__ import annotations

import csv
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.demande_appui import DemandeAppui
from app.models.presence_eleve import PresenceEleve
from app.models.rapport_difficulte_resolution import RapportDifficulteResolution
from app.models.rapport_journalier import RapportJournalier
from app.models.recherche import AssignationRecherche
from app.models.reprise import CorrespondanceReprise, LotReprise, RepriseLigne
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import UserGroupe, UserRole
from app.services import reprise_service

from .conftest import bearer, create_eleve, create_school, create_user, login

API = "/api/v1"
RJ = f"{API}/app/rapports/journalier"
TODAY = date.today()


async def _terrain(client, tag, arrivee_jours: int = 0):
    async with AsyncSessionLocal() as s:
        camp = ProgramSession(name=f"Campagne {tag}", date_debut=date(TODAY.year, 1, 1),
                              date_fin=date(TODAY.year, 12, 31), status=SessionStatus.active)
        s.add(camp)
        await s.commit()
        await s.refresh(camp)
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    eleves = [await create_eleve(school, "CE1", tag, "Titulaire") for _ in range(3)]
    async with AsyncSessionLocal() as s:
        ts = TeacherSession(teacher_id=tuteur.id, session_id=camp.id)
        if arrivee_jours:
            ts.created_at = datetime.now(timezone.utc) - timedelta(days=arrivee_jours)
        s.add(ts)
        for e in eleves:
            (await s.get(type(e), e.id)).session_id = camp.id
        await s.commit()
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id, classes=[str(tuteur.id)])
    admin = await create_user(UserRole.admin, tag)
    return {"camp": camp, "school": school, "tuteur": tuteur, "eleves": eleves, "sup": sup, "admin": admin,
            "ht": bearer(await login(client, tuteur)), "hs": bearer(await login(client, sup)),
            "ha": bearer(await login(client, admin))}


def _rj(tag, **over) -> dict:
    body = {"date_rapport": TODAY.isoformat(), "ief": "IEF", "commune": "C", "ecole": f"E {tag}", "superviseur": "S",
            "nom_tuteur": "T", "nb_absences": 0, "semaine": 2, "jour_cours": 1, "difficultes": '["Aucune"]',
            "directeur_venu": True, "besoin_appui": False}
    body.update(over)
    return body


async def _grille_publiee(client, ha, tag, session_id=None) -> dict:
    r = await client.post(f"{API}/admin/grilles-visite", headers=ha, json={
        "nom": f"Grille {tag}", "session_id": session_id, "items": [
            {"code": "O1", "domaine": "O", "libelle": "Organisation", "type": "echelle", "bareme_max": 3, "ordre": 1},
            {"code": "LL1", "domaine": "LL", "libelle": "Lecture", "type": "echelle", "bareme_max": 3, "ordre": 2},
            {"code": "OUT1", "domaine": "Outils", "libelle": "Guide", "type": "outil", "ordre": 3},
            {"code": "OBS", "domaine": "Notes", "libelle": "Observation", "type": "texte", "ordre": 4},
        ]})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["statut"] == "brouillon" and g["version"] == 1
    r = await client.post(f"{API}/admin/grilles-visite/{g['id']}/publier", headers=ha)
    assert r.status_code == 200 and r.json()["statut"] == "active"
    return r.json()


# ── Présences individuelles (D8 assiduité) ────────────────────────────────

async def test_presences_depuis_rapport_et_annulation(client, tag):
    t = await _terrain(client, tag)
    e1 = t["eleves"][0]
    # Sans liste identifiée : aucun appel déduit (non renseigné ≠ aucune absence).
    r0 = await client.post(RJ, headers=t["ht"], json=_rj(tag, date_rapport=(TODAY - timedelta(days=1)).isoformat()))
    assert r0.status_code == 201
    r = await client.post(RJ, headers=t["ht"], json=_rj(tag, nb_absences=1, absents="A", absents_ids=[str(e1.id)]))
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(PresenceEleve).where(PresenceEleve.session_id == t["camp"].id))).scalars().all()
        assert len(rows) == 3 and {p.date_jour for p in rows} == {TODAY}
        assert {p.eleve_id: p.statut for p in rows}[e1.id] == "absent"
        assert sum(p.statut == "present" for p in rows) == 2

    # Correction admin : absence excusée, jamais écrasée par un rapport ultérieur.
    c = await client.put(f"{API}/admin/presences-eleves", headers=t["ha"], json={
        "date_jour": TODAY.isoformat(), "session_id": str(t["camp"].id), "motif": "Certificat médical",
        "lignes": [{"eleve_id": str(e1.id), "statut": "excuse"}]})
    assert c.status_code == 200 and c.json()["corrigees"] == 1

    # Annulation : les présences issues du rapport disparaissent, l'appel explicite reste.
    a = await client.post(f"{RJ}/{r.json()['id']}/annuler", headers=t["ht"], json={"motif": "Erreur de classe"})
    assert a.status_code == 200, a.text
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(PresenceEleve).where(PresenceEleve.session_id == t["camp"].id))).scalars().all()
        assert [(p.eleve_id, p.statut, p.source) for p in rows] == [(e1.id, "excuse", "appel")]


# ── Grilles versionnées ───────────────────────────────────────────────────

async def test_grille_figee_et_nouvelle_version(client, tag):
    t = await _terrain(client, tag)
    g = await _grille_publiee(client, t["ha"], tag)
    r = await client.put(f"{API}/admin/grilles-visite/{g['id']}", headers=t["ha"], json={"nom": g["nom"], "items": []})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "GRILLE_FIGEE"
    v2 = await client.post(f"{API}/admin/grilles-visite/{g['id']}/nouvelle-version", headers=t["ha"])
    assert v2.status_code == 201 and v2.json()["version"] == 2 and len(v2.json()["items"]) == 4
    assert (await client.post(f"{API}/admin/grilles-visite/{v2.json()['id']}/publier", headers=t["ha"])).status_code == 200
    toutes = {x["id"]: x["statut"] for x in (await client.get(f"{API}/admin/grilles-visite", headers=t["ha"])).json()}
    assert toutes[g["id"]] == "retiree" and toutes[v2.json()["id"]] == "active"
    # Item « echelle » sans barème refusé.
    bad = await client.post(f"{API}/admin/grilles-visite", headers=t["ha"], json={
        "nom": f"X {tag}", "items": [{"code": "A", "domaine": "A", "libelle": "A", "type": "echelle"}]})
    assert bad.status_code == 422


# ── Visites pédagogiques et demandes d'appui (D9.3) ───────────────────────

async def test_visite_scores_perimetre_idempotence_et_appui(client, tag):
    t = await _terrain(client, tag)
    g = await _grille_publiee(client, t["ha"], tag)
    items = {i["code"]: i["id"] for i in g["items"]}
    body = {
        "teacher_id": str(t["tuteur"].id), "grille_id": g["id"], "date_visite": TODAY.isoformat(), "classe": "CE1",
        "duree_minutes": 45, "points_appui": "Bonne gestion", "points_amelioration": "Lecture à renforcer",
        "client_op_id": f"vis-{tag}",
        "reponses": [
            {"item_id": items["O1"], "valeur": 3},
            {"item_id": items["LL1"], "non_applicable": True},
            {"item_id": items["OUT1"], "disponible": False},
            {"item_id": items["OBS"], "texte": "RAS"},
        ],
        "recommandations": [{"titre": "Atelier lecture", "domaine": "LL", "echeance": (TODAY + timedelta(days=10)).isoformat()}],
    }
    # Grilles proposées au superviseur
    gl = await client.get(f"{API}/app/supervisor/grilles-visite", headers=t["hs"])
    assert gl.status_code == 200 and g["id"] in [x["id"] for x in gl.json()]

    r = await client.post(f"{API}/app/supervisor/visites", headers=t["hs"], json=body)
    assert r.status_code == 201, r.text
    v = r.json()
    # Non applicable ≠ 0 : seul O1 compte (3/3) ; outil indisponible compté à part.
    assert v["score_obtenu"] == 3 and v["score_max"] == 3 and v["scores_domaines"]["O"]["taux"] == 100.0
    assert "LL" not in v["scores_domaines"] and v["nb_outils_indisponibles"] == 1 and v["nb_items_manquants"] == 0
    assert len(v["demandes"]) == 1 and v["demandes"][0]["statut"] == "assignee"

    # Rejeu : même visite ; même clé, autre contenu : 409.
    again = await client.post(f"{API}/app/supervisor/visites", headers=t["hs"], json=body)
    assert again.status_code == 201 and again.json()["id"] == v["id"]
    diff = await client.post(f"{API}/app/supervisor/visites", headers=t["hs"], json={**body, "duree_minutes": 50})
    assert diff.status_code == 409

    # Valeur hors barème → 422 détaillé.
    bad = await client.post(f"{API}/app/supervisor/visites", headers=t["hs"], json={
        **body, "client_op_id": None, "reponses": [{"item_id": items["O1"], "valeur": 9}]})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "VISITE_INVALIDE"

    # Hors périmètre : un autre superviseur ne peut pas visiter ce tuteur.
    autre = await create_user(UserRole.superviseur, tag, classes=[str(uuid.uuid4())])
    ho = bearer(await login(client, autre))
    assert (await client.post(f"{API}/app/supervisor/visites", headers=ho,
                              json={**body, "client_op_id": None})).status_code == 403

    # Circuit : résolution sans commentaire refusée, puis résolue et tracée.
    did = v["demandes"][0]["id"]
    no = await client.patch(f"{API}/app/supervisor/demandes-appui/{did}", headers=t["hs"], json={"statut": "resolue"})
    assert no.status_code == 422
    ok = await client.patch(f"{API}/app/supervisor/demandes-appui/{did}", headers=t["hs"],
                            json={"statut": "resolue", "commentaire": "Atelier réalisé le 12"})
    assert ok.status_code == 200 and ok.json()["statut"] == "resolue"
    assert [e["type"] for e in ok.json()["evenements"]] == ["creation", "statut"]
    # Le superviseur hors périmètre ne voit pas la demande.
    assert did not in [d["id"] for d in (await client.get(f"{API}/app/supervisor/demandes-appui", headers=ho,
                                                          params={"statut": "toutes"})).json()]

    # Admin : liste, détail, export CSV (une ligne par réponse, manquants explicites).
    lst = await client.get(f"{API}/admin/visites", headers=t["ha"], params={"teacher_id": str(t["tuteur"].id)})
    assert lst.status_code == 200 and lst.json()["total"] == 1
    exp = await client.get(f"{API}/admin/visites/export", headers=t["ha"], params={"teacher_id": str(t["tuteur"].id)})
    lignes = list(csv.DictReader(exp.text.lstrip("﻿").splitlines(), delimiter=";"))
    assert len(lignes) == 4 and {l["item_code"] for l in lignes} == {"O1", "LL1", "OUT1", "OBS"}
    ann = await client.post(f"{API}/admin/visites/{v['id']}/annuler", headers=t["ha"], json={"motif": "Doublon terrain"})
    assert ann.status_code == 200 and ann.json()["statut"] == "annulee"


async def test_demande_depuis_difficulte_aligne_la_resolution(client, tag):
    t = await _terrain(client, tag)
    r = await client.post(RJ, headers=t["ht"], json=_rj(tag, difficultes='["Manque de matériel"]', besoin_appui=True,
                                                        domaines_appui='["Lecture"]'))
    rid = r.json()["id"]
    body = {"rapport_id": rid, "type": "difficulte", "libelle": "Manque de matériel", "responsable_id": str(t["sup"].id)}
    a = await client.post(f"{API}/admin/demandes-appui/depuis-rapport", headers=t["ha"], json=body)
    b = await client.post(f"{API}/admin/demandes-appui/depuis-rapport", headers=t["ha"], json=body)
    assert a.status_code == 200 and a.json()["id"] == b.json()["id"]  # idempotent
    d = a.json()
    assert d["statut"] == "assignee" and d["beneficiaire_id"] == str(t["tuteur"].id)
    p = await client.patch(f"{API}/admin/demandes-appui/{d['id']}", headers=t["ha"],
                           json={"statut": "resolue", "commentaire": "Kit livré"})
    assert p.status_code == 200
    async with AsyncSessionLocal() as s:
        res = (await s.execute(select(RapportDifficulteResolution).where(
            RapportDifficulteResolution.rapport_id == uuid.UUID(rid)))).scalar_one()
        assert res.resolue and res.commentaire_resolution == "Kit livré"
    # Réouverture motivée possible, retour de la difficulté à « non résolue ».
    ro = await client.patch(f"{API}/admin/demandes-appui/{d['id']}", headers=t["ha"],
                            json={"statut": "en_cours", "commentaire": "Kit incomplet"})
    assert ro.status_code == 200 and ro.json()["cloturee_le"] is None
    vue = await client.get(f"{API}/admin/tableau-de-bord/appui", headers=t["ha"], params={"school_id": str(t["school"].id)})
    assert vue.json()["totaux"]["ouvertes"] == 1
    exp = await client.get(f"{API}/admin/demandes-appui/evenements/export", headers=t["ha"],
                           params={"session_id": str(t["camp"].id)})
    assert exp.status_code == 200 and exp.text.count("\n") >= 4


# ── Calendrier, obligations et vues D8 ────────────────────────────────────

async def test_soumissions_attendues_et_assiduite(client, tag):
    t = await _terrain(client, tag, arrivee_jours=6)
    sid = str(t["camp"].id)
    params = {"session_id": sid, "date_debut": (TODAY - timedelta(days=6)).isoformat()}
    # Rien de configuré : non calculable, jamais 0 %.
    nc = (await client.get(f"{API}/admin/tableau-de-bord/soumissions", headers=t["ha"], params=params)).json()
    assert nc["statut"] == "non_calculable" and "Calendrier" in nc["raison"]

    gen = {"date_debut": (TODAY - timedelta(days=6)).isoformat(), "date_fin": TODAY.isoformat(),
           "jours_semaine": [0, 1, 2, 3, 4, 5, 6],
           "exceptions": [{"date": (TODAY - timedelta(days=3)).isoformat(), "motif": "Jour férié"}]}
    ap = await client.post(f"{API}/admin/campagnes/{sid}/calendrier/generer", headers=t["ha"], json=gen)
    assert ap.json() == {**ap.json(), "jours_generes": 7, "jours_attendus": 6, "applique": False}
    ok = await client.post(f"{API}/admin/campagnes/{sid}/calendrier/generer", headers=t["ha"], json={**gen, "dry_run": False})
    assert ok.json()["applique"] is True
    ob = await client.put(f"{API}/admin/campagnes/{sid}/obligations", headers=t["ha"], json=[
        {"role": "enseignant", "objet": "rapport_journalier", "periodicite": "jour", "quantite": 1, "delai_jours": 0}])
    assert ob.status_code == 200 and ob.json()["obligations"][0]["version"] == 1

    for j in (0, 1):
        await client.post(RJ, headers=t["ht"], json=_rj(
            tag, date_rapport=(TODAY - timedelta(days=j)).isoformat(), nb_absences=1, absents="A",
            absents_ids=[str(t["eleves"][0].id)]))
    v = (await client.get(f"{API}/admin/tableau-de-bord/soumissions", headers=t["ha"], params=params)).json()
    assert v["statut"] == "calcule", v
    ligne = next(l for l in v["lignes"] if l["acteur_id"] == str(t["tuteur"].id))
    assert (ligne["attendu"], ligne["recu"], ligne["manquant"]) == (6, 2, 4)
    rel = await client.get(f"{API}/admin/tableau-de-bord/relances", headers=t["ha"], params={**params, "format": "csv"})
    assert rel.status_code == 200 and t["tuteur"].name in rel.text

    # Changer une obligation incrémente sa version.
    ob2 = await client.put(f"{API}/admin/campagnes/{sid}/obligations", headers=t["ha"], json=[
        {"role": "enseignant", "objet": "rapport_journalier", "periodicite": "jour", "quantite": 1, "delai_jours": 1}])
    assert ob2.json()["obligations"][0]["version"] == 2

    # Assiduité : élève absent 2 jours sur 2 renseignés → alerte (seuil proposé 20 %).
    a = (await client.get(f"{API}/admin/tableau-de-bord/assiduite", headers=t["ha"], params=params)).json()
    assert a["statut"] == "calcule"
    e0 = next(l for l in a["lignes"] if l["eleve_id"] == str(t["eleves"][0].id))
    assert (e0["jours_renseignes"], e0["absences"], e0["taux_absence"]) == (2, 2, 100.0)
    assert e0["jours_non_renseignes"] == 4 and "taux" in e0["alertes"]
    # Seuil propre à la campagne : provenance « campagne ».
    s = await client.put(f"{API}/admin/campagnes/{sid}/seuils", headers=t["ha"],
                         json=[{"code": "absences_taux", "valeur": 101}])
    assert next(x for x in s.json()["seuils"] if x["code"] == "absences_taux")["provenance"] == "campagne"
    a2 = (await client.get(f"{API}/admin/tableau-de-bord/assiduite", headers=t["ha"], params=params)).json()
    assert "taux" not in next(l for l in a2["lignes"] if l["eleve_id"] == str(t["eleves"][0].id))["alertes"]

    ens = (await client.get(f"{API}/admin/tableau-de-bord/vue-ensemble", headers=t["ha"],
                            params={"session_id": sid, "school_id": str(t["school"].id)})).json()
    assert ens["totaux"]["tuteurs_affectes"] == 1 and ens["totaux"]["rapports_recus"] == 2
    sup = (await client.get(f"{API}/admin/tableau-de-bord/supervision", headers=t["ha"],
                            params={"session_id": sid})).json()
    assert sup["lignes"][0]["sans_visite_recente"] is True and sup["totaux"]["visites"] == 0


# ── APP-33 : protocole de recherche et minuteur ───────────────────────────

async def test_protocole_minuteur_controle_serveur(client, tag):
    t = await _terrain(client, tag)
    uid = str(t["tuteur"].id)
    # Sans protocole activé : réglage individuel.
    r = await client.patch(f"{API}/admin/teachers/{uid}", headers=t["ha"], json={"groupe_recherche": "controle"})
    assert r.status_code == 200, r.text
    me = (await client.get(f"{API}/auth/me", headers=t["ht"])).json()
    assert me["app_access"] == "full" and me["minuteur_visible"] is True

    # Protocole activé sur la campagne : contrôle → minuteur masqué par le serveur.
    assert (await client.patch(f"{API}/admin/sessions/{t['camp'].id}", headers=t["ha"],
                               json={"protocole_minuteur": True})).status_code == 200
    me = (await client.get(f"{API}/auth/me", headers=t["ht"])).json()
    assert me["app_access"] == "no_timer" and me["minuteur_visible"] is False
    sync = (await client.get(f"{API}/app/sync", headers=t["ht"])).json()
    assert sync["profile"]["minuteur_visible"] is False

    m = (await client.get(f"{API}/admin/recherche/matrice", headers=t["ha"], params={"session_id": str(t["camp"].id)})).json()
    assert m["totaux"]["incoherents"] == 1 and m["lignes"][0]["app_access_attendu"] == "no_timer"
    ap = (await client.post(f"{API}/admin/recherche/aligner", headers=t["ha"],
                            json={"session_id": str(t["camp"].id), "motif": "Alignement protocole", "dry_run": True})).json()
    assert ap["a_aligner"] == 1 and ap["applique"] is False
    al = (await client.post(f"{API}/admin/recherche/aligner", headers=t["ha"],
                            json={"session_id": str(t["camp"].id), "motif": "Alignement protocole", "dry_run": False})).json()
    assert al["applique"] is True

    # Dérogation : sans motif refusée ; avec motif = exception tracée et respectée.
    sans = await client.patch(f"{API}/admin/teachers/{uid}", headers=t["ha"], json={"app_access": "full"})
    assert sans.status_code == 422 and sans.json()["detail"]["code"] == "EXCEPTION_PROTOCOLE"
    avec = await client.patch(f"{API}/admin/teachers/{uid}", headers=t["ha"],
                              json={"app_access": "full", "motif_protocole": "Dérogation validée par le responsable protocole"})
    assert avec.status_code == 200
    me = (await client.get(f"{API}/auth/me", headers=t["ht"])).json()
    assert me["app_access"] == "full" and me["minuteur_visible"] is True
    hist = (await client.get(f"{API}/admin/recherche/historique", headers=t["ha"], params={"user_id": uid})).json()
    assert hist[0]["exception"] is True and len(hist) >= 3

    # Historique immuable en base.
    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(AssignationRecherche).where(AssignationRecherche.user_id == t["tuteur"].id))).scalars().first()
        with pytest.raises(Exception):
            await s.execute(text("UPDATE assignations_recherche SET motif = 'x' WHERE id = :i"), {"i": row.id})
            await s.commit()


# ── Clés de service (D9.4) ────────────────────────────────────────────────

async def test_cle_service_lecture_pseudonymisee_et_revocation(client, tag):
    t = await _terrain(client, tag)
    c = await client.post(f"{API}/admin/cles-service", headers=t["ha"],
                          json={"nom": f"Shiny {tag}", "portees": ["tableau_bord"]})
    assert c.status_code == 201 and c.json()["valeur"].startswith("nwn_")
    k = {"X-Api-Key": c.json()["valeur"]}
    assert all("valeur" not in x for x in (await client.get(f"{API}/admin/cles-service", headers=t["ha"])).json())

    v = await client.get(f"{API}/service/tableau-de-bord/supervision", headers=k, params={"session_id": str(t["camp"].id)})
    assert v.status_code == 200, v.text
    assert v.json()["lignes"] and all("tuteur" not in l and "ecole" not in l for l in v.json()["lignes"])
    # Portée non accordée → 403 ; sans clé → 401.
    assert (await client.get(f"{API}/service/analytique/tables", headers=k)).status_code == 403
    assert (await client.get(f"{API}/service/tableau-de-bord/appui")).status_code == 401
    await client.post(f"{API}/admin/cles-service/{c.json()['id']}/revoquer", headers=t["ha"])
    assert (await client.get(f"{API}/service/tableau-de-bord/appui", headers=k)).status_code == 401


# ── Reprise de l'historique (D10.2) ───────────────────────────────────────

def _ecrire(dossier: Path, nom: str, lignes: list[dict]) -> None:
    with (dossier / nom).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(lignes[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(lignes)


async def test_reprise_quarantaine_doublons_et_reconciliation(client, tag, tmp_path):
    t = await _terrain(client, tag)
    code_ok, code_inconnu = f"T{tag}", f"X{tag}"
    base = {"date": "2025-03-10", "IEF_label": "IEF A", "ecole_label": "Ecole A", "Superviseur_label": "Sup",
            "Tuteur_label": "Tuteur", "nb_abs": "1", "semaine": "3", "jour": "2", "difficultes_label": "",
            "besoin_appui": "oui", "autres_commentaires": ""}
    _ecrire(tmp_path, "Baseglobale.csv", [
        {**base, "_uuid": f"u1-{tag}", "CodeTuteur": code_ok},
        {**base, "_uuid": f"u1-{tag}", "CodeTuteur": code_ok},                 # doublon
        {**base, "_uuid": f"u2-{tag}", "CodeTuteur": code_inconnu},            # quarantaine
        {**base, "_uuid": f"u3-{tag}", "CodeTuteur": code_ok, "nb_abs": ""},   # compteur manquant
    ])
    _ecrire(tmp_path, "ElevesAbsents.csv", [
        {"_submission__uuid": f"u1-{tag}", "Id_eleve": f"E{tag}", "NomEleve": "A", "date": "2025-03-10"},
        {"_submission__uuid": f"u1-{tag}", "Id_eleve": f"Z{tag}", "NomEleve": "B", "date": "2025-03-10"},
    ])
    _ecrire(tmp_path, "difficulte.csv", [{"_submission__uuid": f"u1-{tag}", "difficulte_label": "Effectif élevé"}])
    _ecrire(tmp_path, "BaseBesoins.csv", [{"_submission__uuid": f"u1-{tag}", "domaines_appui_label": "Lecture"}])

    async with AsyncSessionLocal() as db:
        db.add_all([CorrespondanceReprise(type="tuteur", code_source=code_ok, cible_id=t["tuteur"].id, valide_par="test"),
                    CorrespondanceReprise(type="eleve", code_source=f"E{tag}", cible_id=t["eleves"][0].id, valide_par="test")])
        await db.commit()
        sources = reprise_service.charger_sources(tmp_path)
        analyse = await reprise_service.analyser(db, sources)
        assert analyse["doublons_identifiant"] == 1
        assert [x["code_source"] for x in analyse["a_faire_correspondre"]["tuteur"]] == [code_inconnu]
        lot = LotReprise(nom=f"Lot {tag}", session_id=t["camp"].id, sources=[])
        db.add(lot)
        await db.flush()
        res = await reprise_service.importer(db, lot, sources)
        await db.commit()
        rec = res["reconciliation"]
        total = rec["rapports"]["total"][0]
        assert (total["source"], total["importee"], total["doublon"], total["quarantaine"]) == (4, 1, 1, 2)
        assert rec["toutes_lignes_expliquees"] is True
        assert rec["repetitions"]["absences"] == {"source": 2, "importee": 1, "quarantaine": 1}
        rap = (await db.execute(select(RapportJournalier).where(RapportJournalier.lot_reprise_id == lot.id))).scalar_one()
        assert rap.source == "reprise" and rap.source_submission_id == f"u1-{tag}" and rap.directeur_venu is None
        assert json.loads(rap.difficultes) == ["Effectif élevé"] and json.loads(rap.domaines_appui) == ["Lecture"]
        # Réimport : tout est doublon, rien n'est ajouté.
        lot2 = LotReprise(nom=f"Lot2 {tag}", session_id=t["camp"].id, sources=[])
        db.add(lot2)
        await db.flush()
        res2 = await reprise_service.importer(db, lot2, sources)
        await db.commit()
        assert res2["compteurs"].get("Baseglobale.csv:importee") is None
        n = (await db.execute(select(RepriseLigne).where(RepriseLigne.lot_id == lot.id))).scalars().all()
        assert len(n) == 4 + 2 + 1 + 1

    detail = await client.get(f"{API}/admin/reprise/lots/{lot.id}", headers=t["ha"])
    assert detail.status_code == 200 and detail.json()["reconciliation"]["toutes_lignes_expliquees"]
    q = await client.get(f"{API}/admin/reprise/lots/{lot.id}/lignes", headers=t["ha"], params={"statut": "quarantaine"})
    assert q.json()["total"] == 3


async def test_affectation_datee_conservee(client, tag):
    t = await _terrain(client, tag, arrivee_jours=30)
    autre = await create_user(UserRole.enseignant, tag)
    r = await client.post(f"{API}/admin/sessions/{t['camp'].id}/teachers", headers=t["ha"],
                          json={"teacher_ids": [str(t["tuteur"].id), str(autre.id)]})
    assert r.status_code == 204
    async with AsyncSessionLocal() as s:
        ts = (await s.execute(select(TeacherSession).where(TeacherSession.teacher_id == t["tuteur"].id))).scalar_one()
        assert (datetime.now(timezone.utc) - ts.created_at).days >= 29  # date d'arrivée conservée
    _ = UserGroupe, DemandeAppui
