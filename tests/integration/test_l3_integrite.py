"""Lot L3 du rapport V2 rejoué contre l'API réelle : imports (APP-25/26/39),
validation (APP-28/29/35), historique (APP-15/30/31), périmètre (APP-06/22/23/34)."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date, datetime, time, timedelta, timezone

import openpyxl
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.planning import PlanningSegment
from app.models.school import School
from app.models.session import ProgramSession, SessionStatus
from app.models.user import User, UserRole

from .conftest import bearer, create_user, login

API = "/api/v1"


async def _admin(client, tag):
    return bearer(await login(client, await create_user(UserRole.admin, tag)))


async def _session(tag: str, active: bool = False) -> ProgramSession:
    async with AsyncSessionLocal() as s:
        ps = ProgramSession(name=f"Session {tag}", date_debut=date(2026, 1, 1), date_fin=date(2026, 12, 31),
                            status=SessionStatus.active if active else SessionStatus.inactive)
        s.add(ps)
        await s.commit()
        await s.refresh(ps)
        return ps


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _files(content: bytes, name: str = "f.xlsx", ctype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
    return {"file": (name, content, ctype)}


# ── APP-25 : import global ─────────────────────────────────────────────────

HEAD = ["Code École", "École", "Tuteur", "Téléphone Tuteur", "Classe", "Nom", "Prénom"]


async def test_import_global_ligne_invalide_annulee_seule(client, tag):
    h = await _admin(client, tag)
    occupe = await create_user(UserRole.superviseur, tag, phone=f"77{uuid.uuid4().int % 10**7:07d}")
    code = uuid.uuid4().int % 10**8
    rows = [
        HEAD,
        [code, f"EA {tag}", f"Tut A {tag}", None, "CE1", f"Ndiaye{tag}", "Awa"],
        # Le téléphone du tuteur existe déjà : contrainte SQL → ligne annulée en entier,
        # y compris l'école B qu'elle aurait créée.
        [code + 1, f"EB {tag}", f"Tut B {tag}", occupe.phone, "CE1", f"Fall{tag}", "Modou"],
        [code, f"EA {tag}", f"Tut A {tag}", None, "CE2", f"Sow{tag}", "Ami"],
    ]
    content = _xlsx(rows)

    apercu = (await client.post(f"{API}/admin/import-export/import?dry_run=true", headers=h, files=_files(content))).json()
    assert apercu["dry_run"] is True and apercu["lignes_validees"] == 2 and apercu["lignes_en_erreur"] == 1
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(School).where(School.code_ecole == code))).scalar_one() == 0

    r = (await client.post(f"{API}/admin/import-export/import", headers=h, files=_files(content))).json()
    assert r["dry_run"] is False
    assert (r["lignes_validees"], r["lignes_en_erreur"], r["ecoles_creees"], r["eleves_crees"], r["tuteurs_crees"]) == (2, 1, 1, 2, 1)
    assert r["erreurs"] and r["erreurs"][0].startswith("Ligne 3")
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(School).where(School.code_ecole.in_([code, code + 1])))).scalar_one() == 1
        n = (await s.execute(select(func.count()).select_from(Eleve).where(Eleve.nom.like(f"%{tag}")))).scalar_one()
        assert n == 2

    # Réimport : aucun doublon
    r2 = (await client.post(f"{API}/admin/import-export/import", headers=h, files=_files(content))).json()
    assert r2["eleves_crees"] == 0 and r2["ecoles_creees"] == 0 and r2["ignores"] == 2


async def test_import_global_homonymes_non_fusionnes(client, tag):  # APP-23
    h = await _admin(client, tag)
    code = uuid.uuid4().int % 10**8
    async with AsyncSessionLocal() as s:
        s.add(School(name=f"Ecole Homonyme {tag}", code_ecole=code))
        await s.commit()
    rows = [HEAD, [code + 7, f"ECOLE HOMONYME {tag}", None, None, "CE1", f"Ba{tag}", "Ali"]]
    r = (await client.post(f"{API}/admin/import-export/import", headers=h, files=_files(_xlsx(rows)))).json()
    assert r["lignes_en_erreur"] == 1 and "aucune fusion" in r["erreurs"][0]


# ── APP-26 : import de planning ────────────────────────────────────────────

async def _segments(session_id) -> list[PlanningSegment]:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(PlanningSegment).where(PlanningSegment.session_id == session_id))).scalars().all()


async def test_import_planning_jamais_destructif_sans_confirmation(client, tag):
    h = await _admin(client, tag)
    ps = await _session(tag)
    async with AsyncSessionLocal() as s:
        s.add(PlanningSegment(session_id=ps.id, semaine=2, jour=0,
                              heure_debut=time(8, 0),
                              heure_fin=time(9, 0), matiere="Lecture"))
        await s.commit()
    url = f"{API}/admin/planning/import?session_id={ps.id}&semaine=2"

    # En-têtes seuls : refus, planning inchangé
    r = await client.post(url, headers=h, files=_files(b"jour,heure_debut,heure_fin,matiere\n", "p.csv", "text/csv"))
    assert r.status_code == 422
    assert len(await _segments(ps.id)) == 1

    partiel = (b"jour,heure_debut,heure_fin,matiere\n"
               b"mardi,10:00,11:00,Maths\nmercredi,12:00,11:00,Maths\njeudi,xx,11:00,Lecture\n")
    # Aperçu : périmètre et créneaux annoncés, rien ne change
    r = await client.post(url + "&dry_run=true", headers=h, files=_files(partiel, "p.csv", "text/csv"))
    assert r.status_code == 200, r.text
    ap = r.json()
    assert ap["a_remplacer"] == 1 and ap["valides"] == 1 and len(ap["errors"]) == 2 and "semaine 2" in ap["perimetre"]
    assert len(await _segments(ps.id)) == 1

    # Lignes invalides sans confirmation : 409, planning inchangé
    r = await client.post(url, headers=h, files=_files(partiel, "p.csv", "text/csv"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_IMPORT_PARTIEL"
    assert [s.jour for s in await _segments(ps.id)] == [0]

    # Confirmé : remplacement limité à la semaine annoncée
    r = await client.post(url + "&accepter_erreurs=true", headers=h, files=_files(partiel, "p.csv", "text/csv"))
    assert r.status_code == 200 and r.json()["imported"] == 1 and r.json()["replaced"] == 1
    assert [s.jour for s in await _segments(ps.id)] == [1]


# ── APP-39 : taille des uploads ────────────────────────────────────────────

async def test_classeur_bombe_zip_refuse(client, tag):
    h = await _admin(client, tag)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<x/>")
        zf.writestr("xl/worksheets/sheet1.xml", b"\0" * (60 * 1024 * 1024))
    r = await client.post(f"{API}/admin/import-export/import", headers=h, files=_files(buf.getvalue()))
    assert r.status_code == 413, r.text


async def test_classeur_illisible_400(client, tag):
    h = await _admin(client, tag)
    r = await client.post(f"{API}/admin/import-export/import", headers=h, files=_files(b"pas un zip"))
    assert r.status_code == 400


async def test_content_length_excessif_refuse_avant_lecture(client, tag):
    h = await _admin(client, tag)
    r = await client.post(f"{API}/admin/import-export/import",
                          headers={**h, "Content-Length": str(500 * 1024 * 1024), "Content-Type": "application/octet-stream"},
                          content=b"x")
    assert r.status_code == 413


# ── Contexte terrain commun ────────────────────────────────────────────────

async def _terrain(client, tag, *, classe="CE1"):
    """École + tuteur (classe) + élève + superviseur affecté au tuteur."""
    from .conftest import create_eleve, create_school

    school = await create_school(tag)
    tuteur = await create_user(UserRole.enseignant, tag, school_id=school.id, classes=[classe])
    eleve = await create_eleve(school, classe, tag, "Titulaire")
    sup = await create_user(UserRole.superviseur, tag, school_id=school.id, classes=[str(tuteur.id)])
    return school, tuteur, eleve, sup, bearer(await login(client, sup))


def _rj(tag: str, **over) -> dict:
    body = {
        "date_rapport": date.today().isoformat(), "ief": "IEF", "commune": "C", "ecole": f"E {tag}",
        "superviseur": "S", "nom_tuteur": "T", "nb_absences": 0, "semaine": 2, "jour_cours": 1,
        "difficultes": "[]", "directeur_venu": True, "besoin_appui": False,
    }
    body.update(over)
    return body


# ── APP-28 : validation métier ─────────────────────────────────────────────

async def test_rapport_valeurs_impossibles_422_par_champ(client, tag):
    h = bearer(await login(client, await create_user(UserRole.enseignant, tag)))
    url = f"{API}/app/rapports/journalier"
    cas = {
        "semaine": _rj(tag, semaine=999),
        "jour_cours": _rj(tag, jour_cours=-1),
        "date_rapport": _rj(tag, date_rapport="2099-01-01"),
        "difficultes": _rj(tag, difficultes="pas du json"),
        "absents": _rj(tag, nb_absences=0, absents="Awa"),
    }
    for champ, body in cas.items():
        r = await client.post(url, headers=h, json=body)
        assert r.status_code == 422, (champ, r.text)
    # Borne de campagne (configuration par défaut : 3 jours) → 422 sur jour_cours
    r = await client.post(url, headers=h, json=_rj(tag, jour_cours=5))
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "jour_cours"]


async def test_seance_incoherente_refusee(client, tag):
    h = bearer(await login(client, await create_user(UserRole.enseignant, tag)))
    ps = await _session(tag, active=True)
    debut = datetime.now(timezone.utc) - timedelta(minutes=30)
    base = {"session_id": str(ps.id), "classe": "CE1", "date_seance": debut.isoformat(),
            "started_at": debut.isoformat(), "client_seance_id": f"c-{tag}",
            "finished_at": datetime.now(timezone.utc).isoformat()}
    r = await client.post(f"{API}/app/seances/complete", headers=h, json=base | {"duree_minutes": 300})
    assert r.status_code == 422
    r = await client.post(f"{API}/app/seances/complete", headers=h,
                          json=base | {"duree_minutes": 25, "nb_eleves_total": 10, "nb_eleves_presents": 12})
    assert r.status_code == 422


# ── APP-29 : lot d'évaluations ─────────────────────────────────────────────

async def test_lot_evaluations_prevalide_et_rejouable(client, tag):
    from app.models.evaluation_eleve import EvaluationEleve

    _, _, eleve, sup, h = await _terrain(client, tag)
    url = f"{API}/app/supervisor/evaluations"
    jour = date.today().isoformat()
    ok = {"eleve_id": str(eleve.id), "competence": "Lettres", "resultat": "reussi", "date_eval": jour}

    # Doublon dans le lot → 422, rien d'écrit
    r = await client.post(url, headers=h, json={"evaluations": [ok, ok]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "LOT_INVALIDE"
    assert r.json()["detail"]["erreurs"][0]["index"] == 1
    # Élève inexistant → 422 (pas 500), rien d'écrit
    r = await client.post(url, headers=h, json={"evaluations": [ok, ok | {"eleve_id": str(uuid.uuid4())}]})
    assert r.status_code == 422
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(EvaluationEleve)
                                .where(EvaluationEleve.superviseur_id == sup.id))).scalar_one() == 0

    lot = {"evaluations": [ok, ok | {"competence": "Mots", "resultat": "pas_reussi"}]}
    r1 = (await client.post(url, headers=h, json=lot)).json()
    assert [x["statut"] for x in r1["resultats"]] == ["cree", "cree"]
    r2 = (await client.post(url, headers=h, json=lot)).json()  # rejeu : sans effet
    assert [x["statut"] for x in r2["resultats"]] == ["inchange", "inchange"]
    assert [x["id"] for x in r1["resultats"]] == [x["id"] for x in r2["resultats"]]


# ── APP-06 : périmètre superviseur ─────────────────────────────────────────

async def test_perimetre_superviseur_lectures_et_ecritures(client, tag):
    _, tuteur, eleve, sup, h = await _terrain(client, tag)
    _, autre_tuteur, autre_eleve, _, _ = await _terrain(client, tag + "b")

    ev = {"eleve_id": str(autre_eleve.id), "competence": "Lettres", "resultat": "reussi",
          "date_eval": date.today().isoformat()}
    r = await client.post(f"{API}/app/supervisor/evaluations", headers=h, json={"evaluations": [ev]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "HORS_PERIMETRE"

    r = await client.post(f"{API}/app/supervisor/presences", headers=h, json={
        "date_jour": date.today().isoformat(), "entries": [{"teacher_id": str(autre_tuteur.id), "present": True}]})
    assert r.status_code == 403

    # Classe non affectée au tuteur supervisé → 403
    r = await client.get(f"{API}/app/supervisor/classe-eleves", headers=h,
                         params={"teacher_id": str(tuteur.id), "classe": "CM2"})
    assert r.status_code == 403
    r = await client.get(f"{API}/app/supervisor/classe-eleves", headers=h,
                         params={"teacher_id": str(tuteur.id), "classe": "ce1"})
    assert r.status_code == 200 and [e["id"] for e in r.json()] == [str(eleve.id)]

    # Affectation d'un identifiant qui n'est pas un enseignant → 422
    ha = await _admin(client, tag)
    r = await client.post(f"{API}/admin/superviseurs/{sup.id}/assign-teachers", headers=ha,
                          json={"assigned_teacher_ids": [str(sup.id)]})
    assert r.status_code == 422


# ── APP-35 : e-mails ───────────────────────────────────────────────────────

async def test_email_normalise_et_unique_sans_casse(client, tag):
    from sqlalchemy.exc import IntegrityError

    ha = await _admin(client, tag)
    brut = f"  Awa.{tag}@Test.INVALID "
    r = await client.post(f"{API}/admin/users", headers=ha,
                          json={"name": f"Awa {tag}", "email": brut, "password": "Motdepasse-123!", "role": "coordonnateur"})
    assert r.status_code == 201, r.text
    assert r.json()["email"] == brut.strip().lower()
    r = await client.post(f"{API}/auth/login", json={"identifier": brut.upper(), "password": "Motdepasse-123!"})
    assert r.status_code == 200, r.text
    r = await client.post(f"{API}/admin/users", headers=ha,
                          json={"name": "Autre", "email": brut.strip().upper(), "password": "Motdepasse-123!", "role": "coordonnateur"})
    assert r.status_code == 409
    # Même un insert direct en base ne peut pas créer de variante de casse
    async with AsyncSessionLocal() as s:
        s.add(User(name="x", email=brut.strip().upper(), password_hash="x", role=UserRole.coordonnateur))
        try:
            await s.commit()
            raise AssertionError("doublon accepté")
        except IntegrityError:
            await s.rollback()


# ── APP-15 : archivage ─────────────────────────────────────────────────────

async def test_archivage_conserve_l_historique(client, tag):
    from sqlalchemy.exc import IntegrityError

    from app.models.evaluation_eleve import EvaluationEleve

    school, tuteur, eleve, sup, h = await _terrain(client, tag)
    ev = {"eleve_id": str(eleve.id), "competence": "Lettres", "resultat": "reussi", "date_eval": date.today().isoformat()}
    assert (await client.post(f"{API}/app/supervisor/evaluations", headers=h, json={"evaluations": [ev]})).status_code == 201
    ha = await _admin(client, tag)

    # Suppression définitive refusée tant qu'un historique existe
    r = await client.delete(f"{API}/admin/eleves/{eleve.id}", headers=ha, params={"definitif": "true"})
    assert r.status_code == 409 and r.json()["detail"]["historique"]["evaluations"] == 1
    # « Supprimer » = archiver : l'élève sort des listes, l'évaluation reste
    r = await client.delete(f"{API}/admin/eleves/{eleve.id}", headers=ha)
    assert r.status_code == 200 and r.json()["archive"] is True
    ids = [e["id"] for e in (await client.get(f"{API}/admin/eleves", headers=ha, params={"school_id": str(school.id)})).json()["items"]]
    assert str(eleve.id) not in ids
    ids = [e["id"] for e in (await client.get(f"{API}/admin/eleves", headers=ha,
                                                params={"school_id": str(school.id), "archives": "true"})).json()["items"]]
    assert str(eleve.id) in ids
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(EvaluationEleve)
                                .where(EvaluationEleve.eleve_id == eleve.id))).scalar_one() == 1
    assert (await client.post(f"{API}/admin/eleves/{eleve.id}/restaurer", headers=ha)).status_code == 200

    # Compte archivé : connexion refusée, historique conservé
    r = await client.delete(f"{API}/admin/superviseurs/{sup.id}", headers=ha)
    assert r.status_code == 200 and r.json()["historique_conserve"]["evaluations"] == 1
    assert (await client.post(f"{API}/auth/login", json={"identifier": sup.email, "password": "x"})).status_code in (401, 403)
    assert (await client.get(f"{API}/app/supervisor/eleves", headers=h)).status_code in (401, 403)

    # En base : plus aucune cascade sur les observations
    async with AsyncSessionLocal() as s:
        try:
            await s.execute(School.__table__.delete().where(School.id == school.id))
            await s.commit()
            raise AssertionError("suppression en cascade acceptée")
        except IntegrityError:
            await s.rollback()


# ── APP-30 : réévaluation et audios ────────────────────────────────────────

async def test_reevaluation_conserve_audio_et_resultat(client, tag, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.models.evaluation_resultat import EvaluationResultatHistorique
    from app.models.evaluation_sujet import EvaluationSujet
    from app.models.evaluation_tirage import EvaluationTirage

    monkeypatch.setattr(settings, "UPLOADS_DIR", str(tmp_path))
    _, _, eleve, sup, h = await _terrain(client, tag)
    async with AsyncSessionLocal() as s:
        sujet = EvaluationSujet(titre=f"Sujet {tag}")
        s.add(sujet)
        await s.flush()
        tirage = EvaluationTirage(sujet_id=sujet.id, eleve_id=eleve.id)
        s.add(tirage)
        await s.commit()
        tid = tirage.id
    url = f"{API}/app/supervisor/evaluation-tirages/{tid}/submit"
    r1 = await client.post(url, headers=h, data={"resultat": "pas_reussi"}, files={"audio": ("a.m4a", b"AUDIO-1", "audio/m4a")})
    r2 = await client.post(url, headers=h, data={"resultat": "reussi"}, files={"audio": ("b.m4a", b"AUDIO-2", "audio/m4a")})
    assert r1.status_code == 200 and r2.status_code == 200

    hist = (await client.get(f"{API}/app/supervisor/evaluation-tirages/{tid}/historique", headers=h)).json()
    obs = hist["observations"][0]
    assert obs["resultat"] == "reussi" and obs["corrections"][0]["resultat"] == "pas_reussi"
    anciens = [obs["audio_filename"], obs["corrections"][0]["audio_filename"]]
    assert len(set(anciens)) == 2
    contenus = [(await client.get(f"{API}/app/supervisor/evaluation-audio/{f}", headers=h)).content for f in anciens]
    assert contenus == [b"AUDIO-2", b"AUDIO-1"]
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(EvaluationResultatHistorique)
                                .where(EvaluationResultatHistorique.tirage_id == tid))).scalar_one() == 1


# ── APP-31 : correction et annulation des rapports ─────────────────────────

async def test_rapport_corrige_puis_annule_jamais_supprime(client, tag):
    u = await create_user(UserRole.enseignant, tag)
    h = bearer(await login(client, u))
    url = f"{API}/app/rapports/journalier"
    rid = (await client.post(url, headers=h, json=_rj(tag, client_op_id=f"op-{tag}"))).json()["id"]

    assert (await client.delete(f"{url}/{rid}", headers=h)).status_code == 410
    r = await client.put(f"{url}/{rid}", headers=h, json={"motif": "Erreur de saisie", "nb_absences": 2, "absents": "A, B"})
    assert r.status_code == 200 and r.json()["statut"] == "corrige" and r.json()["version"] == 2
    r = await client.post(f"{url}/{rid}/annuler", headers=h, json={"motif": "Doublon"})
    assert r.status_code == 200 and r.json()["statut"] == "annule"
    assert (await client.put(f"{url}/{rid}", headers=h, json={"motif": "encore", "nb_absences": 1, "absents": "A"})).status_code == 409

    ha = await _admin(client, tag)
    adm = f"{API}/admin/rapports/journalier"
    visibles = [x["id"] for x in (await client.get(adm, headers=ha, params={"teacher_id": str(u.id)})).json()["items"]]
    assert rid not in visibles  # règle commune : hors indicateurs et listes par défaut
    annules = [x["id"] for x in (await client.get(adm, headers=ha, params={"teacher_id": str(u.id), "statut": "annule"})).json()["items"]]
    assert annules == [rid]
    assert (await client.get(f"{adm}/stats", headers=ha, params={"teacher_id": str(u.id)})).json()["total"] == 0
    hist = (await client.get(f"{adm}/{rid}/historique", headers=ha)).json()
    assert [(v["version"], v["action"]) for v in hist] == [(1, "correction"), (2, "annulation")]
    assert hist[0]["contenu"]["nb_absences"] == 0


# ── APP-22 : campagne des séances ──────────────────────────────────────────

async def test_seance_controlee_par_campagne(client, tag):
    from app.models.session import TeacherSession

    u = await create_user(UserRole.enseignant, tag, classes=["CE1"])
    h = bearer(await login(client, u))
    inactive = await _session(tag)
    active = await _session(tag + "a", active=True)
    autre = await _session(tag + "b", active=True)
    now = datetime.now(timezone.utc)
    start = {"classe": "CE1", "date_seance": now.isoformat(), "started_at": now.isoformat()}
    url = f"{API}/app/seances/start"

    r = await client.post(url, headers=h, json=start | {"session_id": str(inactive.id)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CAMPAGNE_INACTIVE"
    r = await client.post(url, headers=h, json=start | {"session_id": str(active.id), "classe": "CM2"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CLASSE_NON_AFFECTEE"
    async with AsyncSessionLocal() as s:
        seg = PlanningSegment(session_id=autre.id, jour=0, heure_debut=time(8), heure_fin=time(9))
        s.add(seg)
        s.add(TeacherSession(teacher_id=u.id, session_id=active.id))
        await s.commit()
        seg_id = seg.id
    r = await client.post(url, headers=h, json=start | {"session_id": str(active.id), "planning_segment_id": str(seg_id)})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CRENEAU_INVALIDE"
    r = await client.post(url, headers=h, json=start | {"session_id": str(autre.id)})
    assert r.status_code == 403
    r = await client.post(url, headers=h, json=start | {"session_id": str(active.id)})
    assert r.status_code == 201, r.text

    # Rapport journalier rattaché à la campagne active du tuteur
    rid = (await client.post(f"{API}/app/rapports/journalier", headers=h, json=_rj(tag))).json()
    assert rid["session_id"] == str(active.id)


# ── APP-23 : homonymes ─────────────────────────────────────────────────────

async def test_ecoles_homonymes_isolees():
    from app.services.school_service import get_matching_school_ids

    suffixe = uuid.uuid4().hex[:6]
    nom = f"Ecole Homo {suffixe}"
    async with AsyncSessionLocal() as s:
        a = School(name=nom, city="Thiès")
        b = School(name=f"EE HOMO {suffixe.upper()}", city="Thies")  # même commune (accents) : doublon d'import
        c = School(name=nom, city="Kaolack")                      # homonyme d'une autre commune
        s.add_all([a, b, c])
        await s.commit()
        ids = set(await get_matching_school_ids(s, a))
    assert ids == {a.id, b.id}


# ── APP-34 : export planning filtré ────────────────────────────────────────

async def test_export_planning_applique_tous_les_filtres(client, tag):
    ha = await _admin(client, tag)
    ps = await _session(tag)
    async with AsyncSessionLocal() as s:
        s.add_all([
            PlanningSegment(session_id=ps.id, semaine=1, jour=0, heure_debut=time(8), heure_fin=time(9), matiere="Lecture"),
            PlanningSegment(session_id=ps.id, semaine=1, jour=1, heure_debut=time(8), heure_fin=time(9), matiere="Maths"),
            PlanningSegment(session_id=ps.id, semaine=1, jour=1, heure_debut=time(10), heure_fin=time(11), matiere="Lecture"),
        ])
        await s.commit()
    r = await client.get(f"{API}/admin/planning/export/csv", headers=ha,
                         params={"session_id": str(ps.id), "jour": 1, "matiere": "Lecture"})
    lignes = [l for l in r.text.lstrip("﻿").splitlines() if l]
    assert r.status_code == 200 and len(lignes) == 2 and "10:00" in lignes[1]
