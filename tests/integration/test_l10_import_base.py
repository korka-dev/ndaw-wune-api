"""Lot L10 — import de la base 2026-2027 par « Import / Export » : FLS, comptes
retrouvés par téléphone, mises à jour, option « nouvelle année »."""
from __future__ import annotations

import io
import uuid

import openpyxl

from app.core.database import AsyncSessionLocal
from app.models.eleve import Eleve
from app.models.school import School
from app.models.user import User, UserRole, UserStatus

from app.core.security import hash_password

from .conftest import PASSWORD, bearer, create_user, login

API = "/api/v1"
URL = f"{API}/admin/import-export/import"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# En-tête de la feuille « Base_application » : les 21 colonnes du format
# import-export, puis des colonnes de provenance que l'import ignore (et FLS).
ENTETE = ["IEF", "Commune", "Code École", "École", "Langue", "Directeur", "Téléphone Directeur",
          "Superviseur", "Téléphone Superviseur", "Tuteur", "Téléphone Tuteur", "Classe", "Niveau",
          "Code Élève", "Nom", "Prénom", "Sexe", "Type", "Groupe Lecture", "Groupe Maths", "Statut",
          "Code IA", "IA", "FLS", "Groupe FLS", "Contrôle source"]


def _tel() -> str:
    return f"77{uuid.uuid4().int % 10**7:07d}"


def _ligne(ief, code, ecole, sup, tel_sup, tut, tel_tut, classe, code_eleve, nom, type_, fls, directeur=None):
    return [ief, "Commune T", code, ecole, None, directeur, None, sup, tel_sup, tut, tel_tut, classe,
            "CP", code_eleve, nom, None, "Fille", type_, None, None, "actif", 1, "IA T", fls, None, "ok"]


def _classeur(lignes) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Base_application"
    ws.append(ENTETE)
    for li in lignes:
        ws.append(li)
    wb.create_sheet("Mode_emploi").append(["Rubrique", "Information"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _envoyer(client, h, contenu, **params):
    r = await client.post(URL, headers=h, params={k: str(v).lower() for k, v in params.items()},
                          files={"file": ("base.xlsx", contenu, XLSX)})
    assert r.status_code == 200, r.text
    return r.json()


async def test_base_2026_2027_nouvelle_annee(client, tag):
    t = tag.upper()
    code = uuid.uuid4().int % 10**8
    async with AsyncSessionLocal() as s:
        # L'année précédente : même école (même code), autre IEF, type non renseigné.
        ecole = School(name=f"EE ANCIENNE {t}", code_ecole=code, region="IEF Ancienne")
        autre = School(name=f"Autre ecole {t}")
        # Même nom qu'une école du fichier, mais l'ancien code : ni reprise ni refus.
        ancien_code = School(name=f"EE SANS TUTEUR {t}", code_ecole=code + 50)
        s.add_all([ecole, autre, ancien_code])
        await s.flush()
        tel_revenu = _tel()
        revenu = User(name=f"Tuteur Revenu {t}", phone=tel_revenu, email=f"revenu.{tag}@test.invalid",
                      password_hash=hash_password(PASSWORD), role=UserRole.enseignant, must_change_password=False,
                      school_id=autre.id, classes=["CE1 A"], niveau=["CE1"], status=UserStatus.actif)
        parti = User(name=f"Tuteur Parti {t}", phone=_tel(), password_hash="x", role=UserRole.enseignant,
                     school_id=autre.id, classes=["CE1 A"], status=UserStatus.actif)
        ancien = Eleve(nom=f"Ancien {t}", classe="CE1 A", school_id=autre.id, code_eleve=f"OLD{t}")
        revient = Eleve(nom="Fall", prenom="Awa", classe="CE1 A", school_id=autre.id, code_eleve=f"E1{t}")
        s.add_all([revenu, parti, ancien, revient])
        await s.commit()
        ids = {"ecole": ecole.id, "revenu": revenu.id, "parti": parti.id, "ancien": ancien.id,
               "revient": revient.id, "autre": autre.id, "ancien_code": ancien_code.id}

    tel_sup, tel_tut2 = _tel(), _tel()
    ief = f"IEF Nouvelle {t}"
    lignes = [
        _ligne(ief, code, f"EE NOUVELLE {t}", f"Sup {t}", tel_sup, f"Tuteur Revenu {t}", tel_revenu,
               "NWN 1", f"E1{t}", f"AWA FALL", "Titulaire", "Oui", directeur=f"Dir {t}"),
        _ligne(ief, code, f"EE NOUVELLE {t}", f"Sup {t}", tel_sup, f"Tuteur Revenu {t}", tel_revenu,
               "NWN 1", f"E2{t}", f"MODOU SARR {t}", "Remplaçant", "Oui"),
        # Homonyme : même nom, même classe, autre code — un autre élève.
        _ligne(ief, code, f"EE NOUVELLE {t}", f"Sup {t}", tel_sup, f"Tuteur Revenu {t}", tel_revenu,
               "NWN 1", f"E5{t}", f"MODOU SARR {t}", "Remplaçant", "Oui"),
        _ligne(ief, code, f"EE NOUVELLE {t}", f"Sup {t}", tel_sup, f"Tuteur Deux {t}", tel_tut2,
               "NWN 2", f"E3{t}", f"BINTA BA {t}", "Titulaire", "Oui"),
        # Classe sans tuteur proposé (cas SOBEME SESSENE du fichier réel).
        _ligne(ief, code + 1, f"EE SANS TUTEUR {t}", None, None, None, None,
               "NWN 1", f"E4{t}", f"ALI DIOP {t}", "Titulaire", "Non"),
    ]
    contenu = _classeur(lignes)
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))

    apercu = await _envoyer(client, h, contenu, dry_run=True, nouvelle_annee=True)
    assert apercu["dry_run"] and apercu["nouvelle_annee"] and apercu["lignes_validees"] == 5
    assert apercu["ecoles_creees"] == 1 and apercu["ecoles_mises_a_jour"] == 1
    assert apercu["comptes_reaffectes"] == 1 and apercu["tuteurs_crees"] == 1 and apercu["superviseurs_crees"] == 1
    assert apercu["eleves_crees"] == 4 and apercu["eleves_mis_a_jour"] == 1 and apercu["homonymes"] == 1
    assert apercu["eleves_archives"] >= 1 and apercu["comptes_archives"] >= 1
    assert apercu["ecoles_archivees"] >= 2 and apercu["classes_archivees"] >= 0
    assert any("EE SANS TUTEUR" in a for a in apercu["avertissements"])
    async with AsyncSessionLocal() as s:  # aperçu : rien n'a changé
        assert (await s.get(School, ids["ecole"])).fls is None
        assert (await s.get(User, ids["parti"])).archived_at is None

    r = await _envoyer(client, h, contenu, nouvelle_annee=True)
    assert r["dry_run"] is False and r["lignes_en_erreur"] == 0
    async with AsyncSessionLocal() as s:
        e = await s.get(School, ids["ecole"])
        assert (e.fls, e.region, e.director, e.name) == (True, ief, f"Dir {t}", f"EE ANCIENNE {t}")
        u = await s.get(User, ids["revenu"])
        assert (u.school_id, u.classes, u.archived_at) == (ids["ecole"], ["NWN 1"], None)
        assert (await s.get(User, ids["parti"])).archived_at is not None
        assert (await s.get(User, ids["parti"])).status == UserStatus.inactif
        assert (await s.get(Eleve, ids["ancien"])).archived_at is not None
        assert (await s.get(School, ids["autre"])).archived_at is not None
        assert (await s.get(School, ids["ancien_code"])).archived_at is not None
        assert (await s.get(School, ids["ecole"])).archived_at is None
        el = await s.get(Eleve, ids["revient"])
        assert (el.school_id, el.classe, el.nom, el.prenom, el.statut_selection) == \
            (ids["ecole"], "NWN 1", "AWA FALL", None, "Titulaire")

    # Le tuteur retrouvé par téléphone se connecte et voit ses élèves de l'année.
    sync = (await client.get(f"{API}/app/sync", headers=bearer(await login(client, u)))).json()
    assert sync["school"]["fls"] is True
    assert [x["nom"] for x in sync["eleves"]] == ["AWA FALL"]          # titulaire
    assert sorted(((x["nom"], x["prenom"]) for x in sync["remplacants"]), key=lambda p: p[1] or "") == \
        [(f"MODOU SARR {t}", None), (f"MODOU SARR {t}", f"(E5{t})")]

    # Compte créé avec le numéro du fichier : mot de passe temporaire habituel, à changer.
    from app.core.config import settings
    r = await client.post(f"{API}/auth/login", json={"identifier": tel_tut2, "password": settings.DEFAULT_USER_PASSWORD})
    assert r.status_code == 200 and r.json()["must_change_password"] is True

    # Réimport : rien ne change (idempotent).
    r2 = await _envoyer(client, h, contenu, nouvelle_annee=True)
    assert (r2["eleves_crees"], r2["eleves_mis_a_jour"], r2["comptes_reaffectes"], r2["ecoles_mises_a_jour"],
            r2["eleves_archives"], r2["comptes_archives"], r2["ignores"]) == (0, 0, 0, 0, 0, 0, 5)
    assert (r2["ecoles_archivees"], r2["classes_archivees"], r2["lignes_en_erreur"]) == (0, 0, 0)


async def test_numero_deja_pris_par_un_autre_role(client, tag):
    t = tag.upper()
    sup = await create_user(UserRole.superviseur, tag, phone=_tel())
    code = uuid.uuid4().int % 10**8
    contenu = _classeur([_ligne("IEF X", code, f"EE X {t}", None, None, f"Tut {t}", sup.phone,
                                "NWN 1", f"Z1{t}", f"NOM {t}", "Titulaire", "Non")])
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))
    r = await _envoyer(client, h, contenu)
    assert r["lignes_en_erreur"] == 1 and "deux rôles" in r["erreurs"][0]


async def test_export_porte_la_colonne_fls(client, tag):
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))
    r = await client.get(f"{API}/admin/import-export/export", headers=h)
    entete = [c.value for c in next(openpyxl.load_workbook(io.BytesIO(r.content)).active.iter_rows(max_row=1))]
    assert entete[-1] == "FLS"


async def _attendre(client, h, tid):
    import asyncio
    for _ in range(200):
        etat = (await client.get(f"{URL}/taches/{tid}", headers=h)).json()
        if etat["statut"] != "en_cours":
            return etat
        await asyncio.sleep(0.05)
    raise AssertionError("import jamais terminé")


async def test_import_en_tache_de_fond_avec_progression(client, tag):
    """Une base complète dépasse le délai du proxy : l'admin suit l'avancement."""
    t = tag.upper()
    code = uuid.uuid4().int % 10**8
    lignes = [_ligne("IEF T", code, f"EE TACHE {t}", None, None, f"Tut {t}", _tel(), "NWN 1",
                     f"T{i}{t}", f"ELEVE {i} {t}", "Titulaire", "Oui") for i in range(250)]
    contenu = _classeur(lignes)
    h = bearer(await login(client, await create_user(UserRole.admin, tag)))

    r = await client.post(f"{URL}/taches", headers=h, params={"dry_run": "true"},
                          files={"file": ("base.xlsx", contenu, XLSX)})
    assert r.status_code == 202 and r.json()["total"] == 250 and r.json()["statut"] == "en_cours"
    etat = await _attendre(client, h, r.json()["id"])
    assert etat["statut"] == "termine" and etat["traitees"] == 250
    assert etat["resultat"]["dry_run"] is True and etat["resultat"]["eleves_crees"] == 250
    async with AsyncSessionLocal() as s:  # aperçu : rien d'enregistré
        from sqlalchemy import func, select
        n = (await s.execute(select(func.count()).select_from(School).where(School.code_ecole == code))).scalar_one()
        assert n == 0

    r = await client.post(f"{URL}/taches", headers=h, files={"file": ("base.xlsx", contenu, XLSX)})
    etat = await _attendre(client, h, r.json()["id"])
    assert etat["statut"] == "termine" and etat["resultat"]["eleves_crees"] == 250
    async with AsyncSessionLocal() as s:
        from sqlalchemy import select
        assert (await s.execute(select(School).where(School.code_ecole == code))).scalar_one().fls is True

    assert (await client.get(f"{URL}/taches/inconnue", headers=h)).status_code == 404


async def test_un_seul_import_a_la_fois(client, tag):
    from app.core.redis import get_redis
    r = await get_redis()
    await r.set("import_global:en_cours", "autre", ex=30)
    try:
        h = bearer(await login(client, await create_user(UserRole.admin, tag)))
        rep = await client.post(f"{URL}/taches", headers=h,
                                files={"file": ("b.xlsx", _classeur([]), XLSX)})
        assert rep.status_code == 409
    finally:
        await r.delete("import_global:en_cours")
