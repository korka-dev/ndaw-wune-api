"""Lot L4 — données, exports et indicateurs (rapport V2, Partie II, Annexe C).

Chaque test travaille dans sa propre campagne fictive et filtre l'export sur
elle : les données des autres tests de la base partagée n'interfèrent pas.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from datetime import date, timedelta


from app.core.database import AsyncSessionLocal
from app.models.evaluation_eleve import EvaluationEleve
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.rapport_journalier import RapportJournalier
from app.models.session import ProgramSession, SessionStatus, TeacherSession
from app.models.user import UserRole

from .conftest import bearer, create_eleve, create_school, create_user, login

API = "/api/v1"
RJ = f"{API}/app/rapports/journalier"
AN = f"{API}/admin/analytique"


async def _campagne(tag: str) -> ProgramSession:
    async with AsyncSessionLocal() as s:
        ps = ProgramSession(name=f"Campagne {tag}", date_debut=date(2026, 1, 1), date_fin=date(2026, 12, 31),
                            status=SessionStatus.active)
        s.add(ps)
        await s.commit()
        await s.refresh(ps)
        return ps


async def _terrain(client, tag):
    """Campagne active + école + tuteur affecté (CE1) + 3 élèves + superviseur."""
    camp = await _campagne(tag)
    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=["CE1"])
    eleves = [await create_eleve(school, "CE1", tag, "Titulaire") for _ in range(3)]
    async with AsyncSessionLocal() as s:
        s.add(TeacherSession(teacher_id=tuteur.id, session_id=camp.id))
        for e in eleves:
            (await s.get(type(e), e.id)).session_id = camp.id
        await s.commit()
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id, classes=[str(tuteur.id)])
    admin = await create_user(UserRole.admin, tag)
    return {
        "camp": camp, "school": school, "tuteur": tuteur, "eleves": eleves, "sup": sup,
        "ht": bearer(await login(client, tuteur)), "ha": bearer(await login(client, admin)),
    }


def _rj(tag: str, **over) -> dict:
    body = {
        "date_rapport": date.today().isoformat(), "ief": "IEF", "commune": "C", "ecole": f"E {tag}",
        "superviseur": "S", "nom_tuteur": "T", "nb_absences": 0, "semaine": 2, "jour_cours": 1,
        "difficultes": '["Aucune"]', "directeur_venu": True, "besoin_appui": False,
    }
    body.update(over)
    return body


async def _pack(client, h, **params) -> tuple[zipfile.ZipFile, dict]:
    r = await client.get(f"{AN}/export", headers=h, params=params)
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    return z, json.loads(z.read("manifest.json"))


def _lire(z: zipfile.ZipFile, nom: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(z.read(nom).decode("utf-8"))))


def _indic(payload: dict, code: str) -> dict:
    return next(i for i in payload["indicateurs"] if i["code"] == code)


# ── Réception : rattachements figés, absents identifiés ───────────────────

async def test_reception_fige_rattachements_et_absents(client, tag):
    t = await _terrain(client, tag)
    e1, e2 = t["eleves"][0], t["eleves"][1]
    r = await client.post(RJ, headers=t["ht"], json=_rj(
        tag, nb_absences=2, absents="A, B", absents_ids=[str(e1.id), str(e2.id)],
        submitted_at="2026-03-02T08:15:00+00:00", client_op_id=f"op-{tag}"))
    assert r.status_code == 201, r.text
    async with AsyncSessionLocal() as s:
        rap = await s.get(RapportJournalier, uuid.UUID(r.json()["id"]))
        assert rap.ecole_id == t["school"].id and rap.superviseur_id == t["sup"].id
        assert rap.session_id == t["camp"].id and rap.effectif_attendu == 3
        assert rap.absents_ids == [str(e1.id), str(e2.id)] and rap.submitted_at is not None

    # Identifiant inconnu → refus explicite ; plus d'identifiés que déclarés → 422.
    bad = await client.post(RJ, headers=t["ht"], json=_rj(tag, nb_absences=1, absents="X",
                                                          absents_ids=[str(uuid.uuid4())]))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "ABSENTS_INCONNUS"
    assert (await client.post(RJ, headers=t["ht"], json=_rj(
        tag, nb_absences=1, absents="A", absents_ids=[str(e1.id), str(e2.id)]))).status_code == 422

    # Un ancien client (sans les nouveaux champs) rejoue sa saisie : même rapport.
    old = _rj(tag, date_rapport=(date.today() - timedelta(days=1)).isoformat(), client_op_id=f"old-{tag}")
    a = await client.post(RJ, headers=t["ht"], json=old)
    b = await client.post(RJ, headers=t["ht"], json=old)
    assert a.status_code == 201 and b.json()["id"] == a.json()["id"]

    # La table individuelle d'absences est liée au rapport et à l'élève (pseudonymisé).
    z, m = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    absences = _lire(z, "tables/absences_eleves.csv")
    assert len(absences) == 2 and {a["methode_rattachement"] for a in absences} == {"identifiant"}
    assert all(a["eleve_id"].startswith("E") and len(a["eleve_id"]) == 21 for a in absences)
    eleves = {e["eleve_id"] for e in _lire(z, "tables/eleves.csv")}
    assert {a["eleve_id"] for a in absences} <= eleves  # jointure possible sans nom
    assert m["mode"] == "pseudonymise" and m["export_id"].startswith("PSEUDO-")


# ── REC02 : difficultés comptées par rapport distinct ─────────────────────

async def test_rec02_pourcentage_sur_rapports_distincts(client, tag):
    t = await _terrain(client, tag)
    d1 = date.today() - timedelta(days=2)
    r1 = await client.post(RJ, headers=t["ht"], json=_rj(
        tag, date_rapport=d1.isoformat(), difficultes='["Bruit", "Manque de matériel", "Retards"]'))
    r2 = await client.post(RJ, headers=t["ht"], json=_rj(tag, difficultes='["Aucune"]'))
    assert r1.status_code == r2.status_code == 201
    ind = (await client.get(f"{AN}/indicateurs", headers=t["ha"], params={"campagne_id": str(t["camp"].id)})).json()
    d = _indic(ind, "rapports_avec_difficulte")
    assert (d["valeur"], d["numerateur"], d["denominateur"]) == (50.0, 1, 2)
    assert d["detail"]["signalements"] == 3

    z, _ = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    sig = _lire(z, "tables/difficultes_signalees.csv")
    assert len(sig) == 4 and sum(1 for x in sig if x["difficulte_code"] == "AUCUNE") == 1
    # REC16 : l'indicateur se recalcule à l'identique depuis le pack.
    par_rapport: dict[str, set] = {}
    for x in sig:
        par_rapport.setdefault(x["rapport_id"], set()).add(x["difficulte_code"] or x["difficulte_libelle"])
    avec = sum(1 for v in par_rapport.values() if v != {"AUCUNE"})
    assert round(100 * avec / len(par_rapport), 1) == d["valeur"]
    assert len(_lire(z, "compat/difficulte.csv")) == 4


# ── REC03 : textes difficiles, formules neutralisées ──────────────────────

async def test_rec03_commentaire_une_cellule_et_formules(client, tag):
    t = await _terrain(client, tag)
    texte = 'Classe « agitée », le maître a dit "attendez"\net la séance a repris — é à ç'
    r = await client.post(RJ, headers=t["ht"], json=_rj(
        tag, has_observations=True, commentaires=texte, besoin_appui=True, domaines_appui="=SOMME(A1)"))
    assert r.status_code == 201
    z, _ = await _pack(client, t["ha"], campagne_id=str(t["camp"].id), nominatif="true")
    com = _lire(z, "tables/commentaires.csv")
    assert len(com) == 1 and com[0]["texte"] == texte
    besoins = _lire(z, "tables/besoins_appui.csv")
    assert besoins[0]["domaine_appui_libelle"] == "'=SOMME(A1)"
    rap = _lire(z, "tables/rapports_journaliers.csv")[0]
    assert rap["nb_absences_declarees"] == "0" and rap["directeur_present_declare"] == "1"
    assert rap["superviseur_id"] == str(t["sup"].id)
    # Mode pseudonymisé : aucun texte libre ni nom.
    zp, _ = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    assert "texte" not in _lire(zp, "tables/commentaires.csv")[0]
    assert "nom_tuteur" not in zp.read("tables/rapports_journaliers.csv").decode().splitlines()[0]
    assert "Commentaires.csv" not in " ".join(zp.namelist())


# ── REC17 : campagne vide — fichiers avec en-têtes, manquant ≠ zéro ──────

async def test_rec17_campagne_vide(client, tag):
    camp = await _campagne(tag)
    ha = bearer(await login(client, await create_user(UserRole.admin, tag)))
    z, m = await _pack(client, ha, campagne_id=str(camp.id))
    noms = set(z.namelist())
    for f in m["fichiers"]:
        assert f["fichier"] in noms
    rapports = z.read("tables/rapports_journaliers.csv").decode().splitlines()
    assert len(rapports) == 1 and rapports[0].startswith("rapport_id,type_rapport,campagne_id")
    statuts = {f["table"]: f["statut"] for f in m["fichiers"]}
    assert statuts["visites_supervision"] == "non_collecte" and statuts["rapports_journaliers"] == "vide"
    assert {"manifest.json", "LISEZMOI.txt", "dictionnaire.csv", "qualite_donnees.csv"} <= noms
    ind = (await client.get(f"{AN}/indicateurs", headers=ha, params={"campagne_id": str(camp.id)})).json()
    assert _indic(ind, "rapports_valides")["valeur"] == 0
    d = _indic(ind, "rapports_avec_difficulte")
    assert d["valeur"] is None and d["denominateur"] == 0          # pas 0 % : rien à mesurer
    p = _indic(ind, "taux_presence_observee")
    assert p["statut"] == "non_calculable" and p["valeur"] is None


# ── APP-36 / REC12 : deux sources d'évaluation unifiées ───────────────────

async def test_evaluations_deux_sources_historique_conserve(client, tag, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOADS_DIR", str(tmp_path))
    t = await _terrain(client, tag)
    e1, e2 = t["eleves"][0], t["eleves"][1]
    async with AsyncSessionLocal() as s:
        s.add(EvaluationEleve(superviseur_id=t["sup"].id, eleve_id=e1.id, session_id=t["camp"].id,
                              competence="lecture", resultat="acquis", date_eval=date.today()))
        sujet = EvaluationSujet(titre=f"Sujet {tag}", session_id=t["camp"].id)
        s.add(sujet)
        await s.flush()
        tirage = EvaluationTirage(sujet_id=sujet.id, eleve_id=e2.id)
        s.add(tirage)
        await s.commit()
        tid, sid = tirage.id, sujet.id
    hs = bearer(await login(client, t["sup"]))
    url = f"{API}/app/supervisor/evaluation-tirages/{tid}/submit"
    for res, audio in (("pas_reussi", b"A1"), ("reussi", b"A2")):
        r = await client.post(url, headers=hs, data={"resultat": res}, files={"audio": ("a.m4a", audio, "audio/m4a")})
        assert r.status_code == 200, r.text

    z, _ = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    ev = _lire(z, "tables/evaluations.csv")
    assert sorted(e["source"] for e in ev) == ["competence", "sujet", "sujet_historique"]
    sujet_row = next(e for e in ev if e["source"] == "sujet")
    hist = next(e for e in ev if e["source"] == "sujet_historique")
    assert sujet_row["instrument_id"] == str(sid) and sujet_row["resultat_harmonise"] == "acquis"
    assert hist["valide"] == "0" and hist["resultat_code"] == "pas_reussi" and hist["remplace_le"]
    assert sujet_row["media_id"] and hist["media_id"] and sujet_row["media_id"] != hist["media_id"]

    ind = (await client.get(f"{AN}/indicateurs", headers=t["ha"], params={"campagne_id": str(t["camp"].id)})).json()
    d = _indic(ind, "eleves_evalues")
    assert d["valeur"] == 2 and d["detail"]["observations"] == 2
    assert d["detail"]["observations_historiques_exclues"] == 1
    assert d["detail"]["eleves_par_source"] == {"competence": 1, "sujet": 1}


# ── REC14 : annulation transmise par l'incrément ──────────────────────────

async def test_rec14_increment_transmet_annulation(client, tag):
    t = await _terrain(client, tag)
    rid = (await client.post(RJ, headers=t["ht"], json=_rj(tag, client_op_id=f"op-{tag}"))).json()["id"]
    p = {"campagne_id": str(t["camp"].id)}
    full = (await client.get(f"{AN}/tables/rapports_journaliers", headers=t["ha"], params=p)).json()
    assert [x["rapport_id"] for x in full["items"]] == [rid]
    borne = full["borne_snapshot"]

    assert (await client.post(f"{RJ}/{rid}/annuler", headers=t["ht"], json={"motif": "Doublon"})).status_code == 200
    inc = (await client.get(f"{AN}/tables/rapports_journaliers", headers=t["ha"],
                            params={**p, "updated_since": borne})).json()
    assert [(x["rapport_id"], x["statut_enregistrement"]) for x in inc["items"]] == [(rid, "annule")]
    # Les tables dérivées suivent la modification de leur rapport.
    dif = (await client.get(f"{AN}/tables/difficultes_signalees", headers=t["ha"],
                            params={**p, "updated_since": borne})).json()
    assert [x["statut_enregistrement"] for x in dif["items"]] == ["annule"]
    # Pagination par curseur déterministe : aucune ligne perdue ni dupliquée.
    for i in range(3):
        await client.post(RJ, headers=t["ht"], json=_rj(tag, date_rapport=(date.today() - timedelta(days=i + 1)).isoformat()))
    vus, curseur = [], None
    while True:
        q = {**p, "limit": 2, **({"curseur": curseur} if curseur else {})}
        page = (await client.get(f"{AN}/tables/rapports_journaliers", headers=t["ha"], params=q)).json()
        vus += [x["rapport_id"] for x in page["items"]]
        curseur = page["curseur_suivant"]
        if not curseur:
            break
    assert len(vus) == len(set(vus)) == 4
    assert (await client.get(f"{AN}/tables/dictionnaire_codes", headers=t["ha"],
                             params={"updated_since": borne})).status_code == 422


# ── REC13 : question modifiée, anciennes réponses interprétables ──────────

async def test_rec13_question_versionnee(client, tag):
    t = await _terrain(client, tag)
    q = await client.post(f"{API}/admin/rapport-questions", headers=t["ha"],
                          json={"label": f"Livres distribués ? {tag}", "type": "oui_non"})
    assert q.status_code == 201 and q.json()["version"] == 1
    qid = q.json()["id"]
    r = await client.post(RJ, headers=t["ht"], json=_rj(tag, reponses_questions=json.dumps({qid: "oui"})))
    assert r.status_code == 201
    up = await client.patch(f"{API}/admin/rapport-questions/{qid}", headers=t["ha"],
                            json={"label": f"Manuels distribués ? {tag}"})
    assert up.json()["version"] == 2
    assert (await client.patch(f"{API}/admin/rapport-questions/{qid}", headers=t["ha"],
                               json={"ordre": 5})).json()["version"] == 2  # l'ordre ne change pas le sens

    z, _ = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    rep = _lire(z, "tables/reponses_questions.csv")
    assert [(x["question_id"], x["question_version"], x["valeur"]) for x in rep] == [(qid, "1", "oui")]
    codes = {x["code"]: x["libelle"] for x in _lire(z, "tables/dictionnaire_codes.csv")}
    assert codes[f"{qid}@1"].startswith("Livres") and codes[f"{qid}@2"].startswith("Manuels")


# ── D11 / REC15 : accès et journalisation ─────────────────────────────────

async def test_acces_nominatif_reserve_et_journalise(client, tag):
    t = await _terrain(client, tag)
    coord = bearer(await login(client, await create_user(UserRole.coordonnateur, tag)))
    p = {"campagne_id": str(t["camp"].id)}
    assert (await client.get(f"{AN}/export", headers=coord, params={**p, "nominatif": "true"})).status_code == 403
    assert (await client.get(f"{AN}/export", headers=coord, params=p)).status_code == 200
    assert (await client.get(f"{AN}/export", headers=t["ht"], params=p)).status_code == 403
    z, m = await _pack(client, t["ha"], **p, nominatif="true")
    assert m["export_id"].startswith("NOMIN-")
    hist = (await client.get(f"{AN}/exports", headers=t["ha"])).json()
    assert hist[0]["export_id"] == m["export_id"] and hist[0]["mode"] == "nominatif"
    assert hist[0]["lignes"]["eleves"] == 3


# ── D3.3 : export CSV journalier avec clés, sans photos ───────────────────

async def test_export_csv_journalier_cles_et_horodatages(client, tag):
    t = await _terrain(client, tag)
    photo = "data:image/jpeg;base64," + "A" * 5000
    rid = (await client.post(RJ, headers=t["ht"], json=_rj(
        tag, photos_classe_url=json.dumps([photo, photo]), reponses_questions='{"q": "x"}'))).json()["id"]
    r = await client.get(f"{API}/admin/rapports/journalier/export/csv", headers=t["ha"],
                         params={"teacher_id": str(t["tuteur"].id)})
    assert r.status_code == 200 and "base64" not in r.text
    row = next(x for x in csv.DictReader(io.StringIO(r.text)) if x["rapport_id"] == rid)
    assert row["campagne_id"] == str(t["camp"].id) and row["ecole_id"] == str(t["school"].id)
    assert row["auteur_id"] == str(t["tuteur"].id) and row["received_at"].endswith("+00:00")
    assert row["nb_photos"] == "2" and row["reponses_questions"] == '{"q": "x"}'


async def test_manifeste_et_dictionnaire_coherents(client, tag):
    t = await _terrain(client, tag)
    await client.post(RJ, headers=t["ht"], json=_rj(tag))
    z, m = await _pack(client, t["ha"], campagne_id=str(t["camp"].id))
    for f in m["fichiers"]:
        n = len(z.read(f["fichier"]).decode().splitlines()) - 1
        assert n == f["lignes"], f["fichier"]
    dico = _lire(z, "dictionnaire.csv")
    vars_rapports = {d["variable"] for d in dico if d["table"] == "rapports_journaliers"}
    entete = z.read("tables/rapports_journaliers.csv").decode().splitlines()[0].split(",")
    assert set(entete) == vars_rapports
    assert m["schema_version"] and m["borne_snapshot"] and m["fuseau"] == "Africa/Dakar"
    assert m["bilan_qualite"]["rapports_exportes"] == 1
