"""Endpoints Admin — Import / Export global des données du programme.

Export : un classeur Excel, une ligne par élève, reprenant toute la hiérarchie
(IEF → école → superviseur → tuteur → classe → élève), sur le même principe
que le fichier de référence BaseNWVFinale2026.

Import : réimporte ce même format (et la feuille « Base_application » de la
base 2026-2027, qui le reprend). Chaque ligne est comparée à l'existant par une
clé stable : ce qui manque est créé, ce qui existe est mis à jour d'après le
fichier. Une cellule vide ne remplace jamais une valeur connue. Réimporter un
export intact ne change rien : l'opération est idempotente.

Clés de correspondance
    École                : code_ecole, repli sur le nom
    Élève                : code_eleve, repli sur (école, classe, nom, prénom)
    Tuteur / superviseur : téléphone (identifiant de connexion), puis (école,
                           nom) — insensible à la casse, aux accents et aux
                           espaces multiples. Un numéro déjà pris par un compte
                           d'un autre rôle refuse la ligne.

Mises à jour (lot L10)
    École  : IEF, commune, directeur, téléphone du directeur, langue, FLS.
    Compte : retrouvé dans une autre école → réaffecté (ses anciennes classes,
             ou les tuteurs d'un superviseur, ne valent plus). Toujours :
             ajout d'une classe manquante à users.classes d'un tuteur, sans
             quoi ses élèves seraient INVISIBLES dans l'app (rattachement sur
             (school_id, classe), cf. CLAUDE.md §2).
    Élève  : école, classe, nom, sexe, type (titulaire / remplaçant), groupes.

Option « nouvelle année » : le fichier fait référence. Les classes des tuteurs
sont exactement celles du fichier, et les élèves, tuteurs et superviseurs
absents du fichier sont ARCHIVÉS (APP-15 : historique conservé, restauration
possible). Rien n'est jamais supprimé.
"""
from __future__ import annotations

import asyncio
import io
import logging
import secrets
import time
import unicodedata
import uuid as uuid_module
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import openpyxl
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl.styles import Font
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.deps import AdminUser, DB
from app.core.export_utils import sanitize_cell
from app.core.upload_utils import read_upload_capped, iter_rows_capped, load_workbook_safe
from app.core.security import hash_password
from app.models.eleve import Eleve
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis, invalidate_sync_caches, invalidate_sync_caches_after_commit
from app.models.user import User, UserRole, UserStatus
from app.schemas.user import _normalize_sn_phone
from app.services.audit_service import journaliser

router = APIRouter(prefix="/import-export", tags=["Admin — Import / Export"])

COLUMNS = [
    "IEF", "Commune", "Code École", "École", "Langue", "Directeur", "Téléphone Directeur",
    "Superviseur", "Téléphone Superviseur",
    "Tuteur", "Téléphone Tuteur",
    "Classe", "Niveau",
    "Code Élève", "Nom", "Prénom", "Sexe",
    "Type", "Groupe Lecture", "Groupe Maths", "Statut",
    # Lot L10 : école avec / sans FLS (Oui / Non, vide = non renseigné). Optionnelle à l'import.
    "FLS",
]


# ── Export ───────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_all(db: DB, _: AdminUser) -> StreamingResponse:
    schools = (await db.execute(select(School))).scalars().all()
    users = (await db.execute(
        select(User).where(User.role.in_((UserRole.enseignant, UserRole.superviseur)))
    )).scalars().all()
    classes = (await db.execute(select(SchoolClasse))).scalars().all()
    eleves = (await db.execute(select(Eleve).options(selectinload(Eleve.school)))).scalars().all()

    teachers_by_school: dict[uuid_module.UUID, list[User]] = {}
    supervisors_by_school: dict[uuid_module.UUID, list[User]] = {}
    for u in users:
        if u.school_id is None:
            continue
        bucket = teachers_by_school if u.role == UserRole.enseignant else supervisors_by_school
        bucket.setdefault(u.school_id, []).append(u)

    niveau_by_class: dict[tuple[uuid_module.UUID, str], str] = {
        (c.school_id, c.name): c.niveau for c in classes
    }

    def tuteur_for(school_id: Optional[uuid_module.UUID], classe: str) -> Optional[User]:
        if school_id is None:
            return None
        for t in teachers_by_school.get(school_id, []):
            if classe in (t.classes or []):
                return t
        teachers = teachers_by_school.get(school_id, [])
        return teachers[0] if len(teachers) == 1 else None

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Élèves"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for e in sorted(eleves, key=lambda x: (x.school.name if x.school else "", x.classe, x.nom)):
        school = e.school
        sup_list = supervisors_by_school.get(school.id, []) if school else []
        sup_name = " | ".join(s.name for s in sup_list) if sup_list else None
        tut = tuteur_for(school.id, e.classe) if school else None
        niveau = niveau_by_class.get((school.id, e.classe)) if school else None

        ws.append([sanitize_cell(v) for v in [
            school.region if school else None,
            school.city if school else None,
            school.code_ecole if school else None,
            school.name if school else None,
            school.langue if school else None,
            school.director if school else None,
            school.director_phone if school else None,
            sup_name,
            (sup_list[0].phone if sup_list else None),
            tut.name if tut else None,
            tut.phone if tut else None,
            e.classe,
            niveau,
            e.code_eleve,
            e.nom,
            e.prenom,
            e.genre,
            e.statut_selection,
            e.groupe_lecture,
            e.groupe_maths,
            e.statut,
            ({True: "Oui", False: "Non"}.get(school.fls) if school else None),
        ]])

    for i, col in enumerate(COLUMNS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(12, min(28, len(col) + 4))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=export_ndawwune.xlsx"},
    )


# ── Import ───────────────────────────────────────────────────────────────────

class ImportSummary(BaseModel):
    # Rapport V2, APP-25 : les compteurs ne reflètent QUE des lignes réellement
    # enregistrées (chaque ligne est validée dans son propre point de
    # sauvegarde ; une ligne en erreur est annulée en entier, rien n'est compté).
    dry_run:            bool = False   # aperçu : rien n'a été enregistré
    nouvelle_annee:     bool = False   # le fichier fait référence (lot L10)
    lignes_lues:        int = 0
    lignes_validees:    int = 0
    lignes_en_erreur:   int = 0
    ecoles_creees:      int = 0
    ecoles_mises_a_jour: int = 0  # IEF, commune, directeur, langue ou FLS changés
    superviseurs_crees: int = 0
    tuteurs_crees:      int = 0
    comptes_reaffectes: int = 0  # compte existant (retrouvé par téléphone) changé d'école ou de classes
    classes_creees:     int = 0
    classes_ajoutees_tuteurs: int = 0  # classes ajoutées à un tuteur existant
    eleves_crees:       int = 0
    eleves_mis_a_jour:  int = 0
    ignores:            int = 0  # lignes dont l'élève existait déjà, à l'identique
    homonymes:          int = 0  # élèves de même nom dans une classe : code ajouté au prénom
    eleves_archives:    int = 0  # nouvelle année : élèves absents du fichier
    comptes_archives:   int = 0  # nouvelle année : tuteurs / superviseurs absents du fichier
    erreurs:            list[str] = []
    erreurs_tronquees:  int = 0  # erreurs non listées (au-delà de 50)
    avertissements:     list[str] = []


_COMPTEURS = ("ecoles_creees", "ecoles_mises_a_jour", "superviseurs_crees", "tuteurs_crees",
              "comptes_reaffectes", "classes_creees", "classes_ajoutees_tuteurs", "eleves_crees",
              "eleves_mis_a_jour", "ignores", "homonymes")


class LigneRefusee(Exception):
    """Erreur métier d'une ligne : message destiné à l'utilisateur."""


def _s(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # 771234567.0 lu par Excel → « 771234567 »
    v = str(v).strip()
    return v or None


def _tel(v) -> Optional[str]:
    """Téléphone du fichier → 9 chiffres ; une valeur invalide refuse la ligne."""
    try:
        return _normalize_sn_phone(_s(v))
    except ValueError:
        raise LigneRefusee(f"téléphone « {_s(v)} » invalide (9 chiffres attendus).")


def _fls(v) -> Optional[bool]:
    """Colonne « FLS » : Oui / Non (aussi « avec » / « sans »). Vide = non renseigné."""
    s = _nkey(v)
    if s in ("OUI", "O", "AVEC", "AVEC FLS", "FLS", "1", "TRUE", "VRAI"):
        return True
    if s in ("NON", "N", "SANS", "SANS FLS", "0", "FALSE", "FAUX"):
        return False
    return None


def _nkey(v) -> str:
    """Clé de correspondance d'un nom de personne : insensible à la casse, aux
    accents et aux espaces multiples.

    Les noms saisis à la main dans un fichier réimporté ne portent pas toujours
    les accents du dashboard (« SATOU SENE » pour « SATOU SÉNE »), et certaines
    fiches ont un double espace (« MODOU  FALL »). Sans cette neutralisation, un
    nom déjà connu est vu comme nouveau et un compte en double est créé.
    """
    if v is None:
        return ""
    s = unicodedata.normalize("NFD", str(v))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return " ".join(s.split()).upper()


def _cle_tuteur(tel: Optional[str], code_ecole, ecole: Optional[str], nom: str):
    """Identifie un tuteur du fichier : son téléphone, sinon (école, nom)."""
    return tel or ("nom", _s(code_ecole) or _nkey(ecole), _nkey(nom))


def _unique_placeholder_phone(taken: set[str]) -> str:
    """Numéro à 9 chiffres garanti libre, pour un compte créé sans contact fourni
    dans le fichier — doit être changé manuellement ensuite."""
    while True:
        candidate = "90" + "".join(secrets.choice("0123456789") for _ in range(7))
        if candidate not in taken:
            taken.add(candidate)
            return candidate


@router.post("/import", response_model=ImportSummary)
async def import_all(
    db: DB,
    admin: AdminUser,
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Aperçu : tout est vérifié puis annulé, rien n'est enregistré."),
    nouvelle_annee: bool = Query(False, description=(
        "Nouvelle année scolaire : le fichier fait référence. Les classes des tuteurs du fichier sont "
        "remplacées par celles du fichier, et les élèves, tuteurs et superviseurs absents du fichier "
        "sont archivés (historique conservé, restauration possible).")),
) -> ImportSummary:
    """Import global ligne par ligne (rapport V2, APP-25 ; lot L10).

    Politique : import PARTIEL par ligne. Chaque ligne s'exécute dans un point
    de sauvegarde ; une ligne refusée (donnée invalide, contrainte SQL) est
    annulée en entier et listée, les autres sont enregistrées. Le résumé
    correspond exactement à l'état en base. `dry_run=true` exécute le même
    traitement puis annule tout : c'est l'aperçu affiché avant confirmation.

    Lot L10 (base 2026-2027) :
    - les comptes sont d'abord retrouvés par TÉLÉPHONE (l'identifiant de
      connexion), puis par (école, nom) ; un compte retrouvé dans une autre
      école y est réaffecté ;
    - les écoles et élèves existants sont mis à jour d'après le fichier (IEF,
      commune, directeur, langue, FLS ; classe, type, sexe de l'élève). Une
      cellule vide ne remplace jamais une valeur connue ;
    - la colonne optionnelle « FLS » (Oui / Non) renseigne l'école avec / sans FLS.
    """
    rows = await asyncio.to_thread(_lire_lignes, await read_upload_capped(file))
    return await executer_import(db, admin, rows, dry_run, nouvelle_annee)


def _lire_lignes(content: bytes) -> list[tuple]:
    """Lignes du premier onglet (« Base_application » dans la base 2026-2027)."""
    wb = load_workbook_safe(content)
    try:
        return list(iter_rows_capped(wb.worksheets[0]))
    finally:
        wb.close()


Progression = Optional[Callable[[str, int, int], Awaitable[None]]]


async def executer_import(db, admin: User, rows: list[tuple], dry_run: bool, nouvelle_annee: bool,
                          progression: Progression = None) -> ImportSummary:
    """Traitement de l'import (requête directe ou tâche de fond).

    `progression(phase, traitees, total)` est appelée régulièrement pour la
    barre de progression de l'admin. En aperçu (`dry_run`), tout est annulé ;
    sinon l'appelant valide la transaction.
    """
    async def avancer(phase: str, traitees: int, total: int) -> None:
        if progression is not None:
            await progression(phase, traitees, total)

    if not rows:
        return ImportSummary(dry_run=dry_run, nouvelle_annee=nouvelle_annee, erreurs=["Fichier vide."])

    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = {name: header.index(name) for name in COLUMNS if name in header}
    missing = [c for c in ("École", "Classe", "Nom") if c not in idx]
    if missing:
        return ImportSummary(dry_run=dry_run, nouvelle_annee=nouvelle_annee,
                             erreurs=[f"Colonnes requises manquantes : {missing}. "
                                      f"Réexportez depuis « Import / Export » pour garantir le bon format."])

    def get(row: tuple, name: str):
        i = idx.get(name)
        if i is None or i >= len(row):
            return None
        return row[i]

    summary = ImportSummary(dry_run=dry_run, nouvelle_annee=nouvelle_annee)

    # Ce que le fichier contient, même si sa ligne est refusée : en mode
    # « nouvelle année », un élève ou un compte cité n'est jamais archivé.
    codes_fichier: set[str] = set()
    tels_fichier: set[str] = set()
    # Nouvelle année : classes et niveaux de chaque tuteur d'après le fichier.
    classes_tuteurs: dict = {}
    for row in rows[1:]:
        if c := _s(get(row, "Code Élève")):
            codes_fichier.add(c)
        if (tn := _s(get(row, "Tuteur"))) and (cl := _s(get(row, "Classe"))):
            try:
                tt = _normalize_sn_phone(_s(get(row, "Téléphone Tuteur")))
            except ValueError:
                tt = None
            cles, nivs = classes_tuteurs.setdefault(
                _cle_tuteur(tt, get(row, "Code École"), get(row, "École"), tn), (set(), set()))
            cles.add(cl)
            nivs.add(_s(get(row, "Niveau")) or cl.split()[0])
        for col in ("Téléphone Tuteur", "Téléphone Superviseur"):
            try:
                if t := _normalize_sn_phone(_s(get(row, col))):
                    tels_fichier.add(t)
            except ValueError:
                pass

    # Index en mémoire, rechargés depuis la base après une ligne annulée (les
    # objets créés ou modifiés par cette ligne n'existent plus).
    schools_by_code: dict = {}
    schools_by_name: dict[str, list[School]] = {}
    school_by_director_phone: dict[str, School] = {}
    users_by_school: dict[tuple[Optional[uuid_module.UUID], str, UserRole], User] = {}
    users_sans_ecole: dict[tuple[str, UserRole], User] = {}
    users_by_phone: dict[str, User] = {}
    taken_phones: set[str] = set()
    classes_by_key: dict = {}
    eleves_by_code: dict = {}
    eleves_by_identity: dict = {}
    # Objets touchés par le fichier (validés) : comptes déjà remis à zéro pour
    # l'année, écoles déjà comptées comme mises à jour, élèves vus.
    comptes_vus: set[uuid_module.UUID] = set()
    ecoles_vues: set[uuid_module.UUID] = set()
    eleves_vus: set[uuid_module.UUID] = set()

    async def charger() -> None:
        def _all(model):
            return select(model).execution_options(populate_existing=True)

        schools_db = (await db.execute(_all(School))).scalars().all()
        schools_by_code.clear(); schools_by_name.clear(); school_by_director_phone.clear()
        for sc in schools_db:
            if sc.code_ecole is not None:
                schools_by_code[sc.code_ecole] = sc
            schools_by_name.setdefault(_nkey(sc.name), []).append(sc)
            if sc.director_phone:
                school_by_director_phone[sc.director_phone] = sc

        users_db = (await db.execute(_all(User))).scalars().all()
        # Deux personnes différentes peuvent porter le même nom dans deux écoles
        # différentes (c'est le cas en base pour AISSATA NDIAYE). La clé de
        # correspondance est donc (école, nom, rôle) ; on retombe sur les comptes
        # sans école, puis on crée.
        users_by_school.clear(); users_sans_ecole.clear(); taken_phones.clear(); users_by_phone.clear()
        for u in users_db:
            users_by_school[(u.school_id, _nkey(u.name), u.role)] = u
            if u.school_id is None:
                users_sans_ecole[(_nkey(u.name), u.role)] = u
            if u.phone:
                users_by_phone[u.phone] = u
        taken_phones.update(u.phone for u in users_db if u.phone)

        classes_db = (await db.execute(_all(SchoolClasse))).scalars().all()
        classes_by_key.clear()
        classes_by_key.update({(c.school_id, c.name): c for c in classes_db})

        eleves_db = (await db.execute(_all(Eleve))).scalars().all()
        eleves_by_code.clear(); eleves_by_identity.clear()
        eleves_by_code.update({e.code_eleve: e for e in eleves_db if e.code_eleve})
        eleves_by_identity.update({
            (e.school_id, e.classe, (e.nom or "").strip().upper(), (e.prenom or "").strip().upper()): e
            for e in eleves_db
        })

    await charger()

    hash_defaut: list[str] = []

    def mot_de_passe_initial(numero_reel: bool) -> str:
        """Compte créé avec le numéro du fichier : mot de passe temporaire habituel
        (DEFAULT_USER_PASSWORD, à changer à la première connexion), comme un compte
        créé depuis le dashboard. Numéro provisoire : mot de passe aléatoire (le
        compte ne peut pas servir tant qu'un vrai numéro n'est pas saisi).
        Le hachage bcrypt est calculé une fois par import (~0,25 s chacun)."""
        if not numero_reel:
            return hash_password(secrets.token_urlsafe(12))
        if not hash_defaut:
            hash_defaut.append(hash_password(settings.DEFAULT_USER_PASSWORD))
        return hash_defaut[0]

    def trouver_ecole(code_ecole: Optional[int], nom: str) -> Optional[School]:
        """APP-23 : jamais de fusion sur le seul nom quand il est ambigu.

        - code fourni et connu → cette école ;
        - code fourni, inconnu, mais une école du même nom porte un AUTRE code
          → refus (homonymes possibles dans deux communes) ;
        - sans code : rattachement au nom seulement s'il est unique.
        """
        if code_ecole is not None and code_ecole in schools_by_code:
            return schools_by_code[code_ecole]
        homonymes = schools_by_name.get(_nkey(nom), [])
        if code_ecole is not None:
            autres = [h for h in homonymes if h.code_ecole is not None and h.code_ecole != code_ecole]
            if autres:
                raise LigneRefusee(
                    f"l'école « {nom} » (code {code_ecole}) porte le nom d'une école existante de code "
                    f"{autres[0].code_ecole} : vérifiez le code, aucune fusion automatique."
                )
            sans_code = [h for h in homonymes if h.code_ecole is None]
            if len(sans_code) > 1:
                raise LigneRefusee(f"plusieurs écoles « {nom} » sans code : précisez laquelle dans le dashboard.")
            return sans_code[0] if sans_code else None
        if len(homonymes) > 1:
            raise LigneRefusee(f"plusieurs écoles s'appellent « {nom} » : renseignez la colonne « Code École ».")
        return homonymes[0] if homonymes else None

    def trouver_compte(school_id, nom: str, role: UserRole) -> Optional[User]:
        u = users_by_school.get((school_id, _nkey(nom), role))
        if u is not None:
            return u
        u = users_sans_ecole.get((_nkey(nom), role))
        if u is not None:            # compte orphelin : on le rattache à l'école
            u.school_id = school_id
            users_by_school[(school_id, _nkey(nom), role)] = u
            users_sans_ecole.pop((_nkey(nom), role), None)
        return u

    def enregistrer_compte(u: User) -> None:
        users_by_school[(u.school_id, _nkey(u.name), u.role)] = u
        if u.phone:
            users_by_phone[u.phone] = u

    def ajouter_classe(tut: User, classe: str, niveau: Optional[str]) -> bool:
        """Complète users.classes / users.niveau d'un enseignant.

        Sans cela, un élève ajouté dans une classe que le tuteur n'a pas encore
        est créé en base mais reste INVISIBLE dans l'app : le rattachement se
        fait sur (school_id, classe) et la classe manquerait du tableau.
        """
        change = False
        if classe not in (tut.classes or []):
            tut.classes = sorted([*(tut.classes or []), classe])
            change = True
        if niveau and niveau not in (tut.niveau or []):
            tut.niveau = sorted([*(tut.niveau or []), niveau])
            change = True
        return change

    def compte_par_telephone(phone: Optional[str], role: UserRole, nom: str) -> Optional[User]:
        """Le téléphone est l'identifiant de connexion : il prime sur le nom."""
        if not phone:
            return None
        u = users_by_phone.get(phone)
        if u is not None and u.role != role:
            raise LigneRefusee(
                f"le numéro {phone} ({nom}) est déjà celui du compte {u.role.value} « {u.name} » : "
                f"un même numéro ne peut pas servir à deux rôles. Corrigez le fichier ou le compte.")
        return u

    def preparer_compte(u: User, school: School, vus_ligne: set, cle_tuteur=None) -> bool:
        """Réaffecte un compte existant à l'école du fichier. Retourne True si
        sa fiche change (école, classes remises à zéro, restauration).

        - autre école : le compte suit le fichier, ses anciennes classes (ou,
          pour un superviseur, ses anciens tuteurs) ne valent plus ;
        - nouvelle année : à la première ligne qui le cite, ses classes
          repartent de zéro (celles du fichier s'ajoutent ensuite) ;
        - compte archivé cité par le fichier : restauré.
        """
        if u.id in comptes_vus or u.id in vus_ligne:
            return False
        avant = (u.school_id, tuple(u.classes or ()), tuple(u.niveau or ()), u.archived_at is None)
        if u.archived_at is not None:
            u.archived_at = None
            u.status = UserStatus.actif
        if nouvelle_annee and u.role == UserRole.enseignant and cle_tuteur in classes_tuteurs:
            u.school_id = school.id
            cles, nivs = classes_tuteurs[cle_tuteur]
            u.classes, u.niveau = sorted(cles), sorted(nivs)
        elif u.school_id != school.id or nouvelle_annee:
            u.school_id = school.id
            u.classes = None
            if u.role == UserRole.enseignant:
                u.niveau = None
        vus_ligne.add(u.id)
        return avant != (u.school_id, tuple(u.classes or ()), tuple(u.niveau or ()), u.archived_at is None)

    def maj_ecole(school: School, row: tuple) -> bool:
        """Met à jour l'école d'après le fichier ; une cellule vide ne remplace rien."""
        change = False
        for attr, col in (("region", "IEF"), ("city", "Commune"), ("director", "Directeur")):
            v = _s(get(row, col))
            if v and getattr(school, attr) != v:
                setattr(school, attr, v)
                change = True
        langue = (_s(get(row, "Langue")) or "").lower() or None
        if langue and school.langue != langue:
            school.langue, change = langue, True
        fls = _fls(get(row, "FLS"))
        if fls is not None and school.fls is not fls:
            school.fls, change = fls, True
        try:
            tel_dir = _normalize_sn_phone(_s(get(row, "Téléphone Directeur")))
        except ValueError:
            tel_dir = None
        if tel_dir and school.director_phone != tel_dir:
            autre = school_by_director_phone.get(tel_dir)
            if autre is None or autre.id == school.id:
                if school.director_phone:
                    school_by_director_phone.pop(school.director_phone, None)
                school.director_phone, change = tel_dir, True
                school_by_director_phone[tel_dir] = school
        return change

    total = len(rows) - 1
    await avancer("lignes", 0, total)
    for line_num, row in enumerate(rows[1:], start=2):
        if (line_num - 2) % 100 == 0 and line_num > 2:
            await avancer("lignes", line_num - 2, total)
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        summary.lignes_lues += 1
        delta = dict.fromkeys(_COMPTEURS, 0)
        vus_ligne: set = set()
        ecoles_ligne: set = set()
        eleves_ligne: set = set()
        try:
            async with db.begin_nested():
                ecole_name = _s(get(row, "École"))
                classe = _s(get(row, "Classe"))
                nom_eleve = _s(get(row, "Nom"))
                if not ecole_name or not classe or not nom_eleve:
                    raise LigneRefusee("École, Classe et Nom sont requis.")

                # 1) École
                code_ecole_raw = get(row, "Code École")
                try:
                    code_ecole = int(code_ecole_raw) if code_ecole_raw not in (None, "") else None
                except (TypeError, ValueError):
                    raise LigneRefusee(f"Code École « {code_ecole_raw} » invalide (nombre attendu).")
                school = trouver_ecole(code_ecole, ecole_name)
                if school is None:
                    tel_dir = _tel(get(row, "Téléphone Directeur"))
                    if tel_dir and tel_dir in school_by_director_phone:
                        tel_dir = None  # déjà celui d'une autre école : laissé vide
                    school = School(
                        id=uuid_module.uuid4(),
                        name=ecole_name,
                        code_ecole=code_ecole,
                        region=_s(get(row, "IEF")),
                        city=_s(get(row, "Commune")),
                        langue=(_s(get(row, "Langue")) or "").lower() or None,
                        director=_s(get(row, "Directeur")),
                        director_phone=tel_dir,
                        fls=_fls(get(row, "FLS")),
                    )
                    db.add(school)
                    await db.flush()
                    if code_ecole is not None:
                        schools_by_code[code_ecole] = school
                    schools_by_name.setdefault(_nkey(ecole_name), []).append(school)
                    if tel_dir:
                        school_by_director_phone[tel_dir] = school
                    ecoles_ligne.add(school.id)
                    delta["ecoles_creees"] += 1
                elif school.id not in ecoles_vues:
                    if school.archived_at is not None:
                        school.archived_at = None  # citée par le fichier : restaurée
                        delta["ecoles_mises_a_jour"] += 1
                    elif maj_ecole(school, row):
                        delta["ecoles_mises_a_jour"] += 1
                    ecoles_ligne.add(school.id)
                    await db.flush()

                # 2) Superviseur (téléphone d'abord, puis nom dans l'école)
                sup_name = _s(get(row, "Superviseur"))
                if sup_name:
                    noms = [n.strip() for n in sup_name.split("|") if n.strip()]
                    tel_sup = _tel(get(row, "Téléphone Superviseur")) if len(noms) == 1 else None
                    for one_name in noms:
                        sup = compte_par_telephone(tel_sup, UserRole.superviseur, one_name) \
                            or trouver_compte(school.id, one_name, UserRole.superviseur)
                        if sup is None:
                            phone = tel_sup or _unique_placeholder_phone(taken_phones)
                            sup = User(
                                id=uuid_module.uuid4(), name=one_name,
                                phone=phone,
                                password_hash=mot_de_passe_initial(bool(tel_sup)),
                                role=UserRole.superviseur, school_id=school.id,
                                must_change_password=True,
                            )
                            db.add(sup)
                            await db.flush()
                            enregistrer_compte(sup)
                            taken_phones.add(phone)
                            vus_ligne.add(sup.id)
                            delta["superviseurs_crees"] += 1
                        elif preparer_compte(sup, school, vus_ligne):
                            await db.flush()
                            enregistrer_compte(sup)
                            delta["comptes_reaffectes"] += 1

                # 3) Tuteur / enseignant
                niveau = _s(get(row, "Niveau")) or classe.split()[0]
                tut_name = _s(get(row, "Tuteur"))
                if tut_name:
                    tel_tut = _tel(get(row, "Téléphone Tuteur"))
                    tut = compte_par_telephone(tel_tut, UserRole.enseignant, tut_name) \
                        or trouver_compte(school.id, tut_name, UserRole.enseignant)
                    if tut is None:
                        phone = tel_tut or _unique_placeholder_phone(taken_phones)
                        tut = User(
                            id=uuid_module.uuid4(), name=tut_name,
                            phone=phone,
                            password_hash=mot_de_passe_initial(bool(tel_tut)),
                            role=UserRole.enseignant, school_id=school.id,
                            classes=[classe], niveau=[niveau],
                            must_change_password=True,
                        )
                        db.add(tut)
                        await db.flush()
                        enregistrer_compte(tut)
                        taken_phones.add(phone)
                        vus_ligne.add(tut.id)
                        delta["tuteurs_crees"] += 1
                    else:
                        reaffecte = preparer_compte(
                            tut, school, vus_ligne, _cle_tuteur(tel_tut, code_ecole_raw, ecole_name, tut_name))
                        ajoute = ajouter_classe(tut, classe, niveau)
                        if reaffecte or ajoute:
                            await db.flush()
                            enregistrer_compte(tut)
                        if reaffecte:
                            delta["comptes_reaffectes"] += 1
                        elif ajoute:
                            delta["classes_ajoutees_tuteurs"] += 1

                # 4) Classe (school_classes)
                class_key = (school.id, classe)
                if class_key not in classes_by_key:
                    new_classe = SchoolClasse(id=uuid_module.uuid4(), school_id=school.id, name=classe, niveau=niveau)
                    db.add(new_classe)
                    await db.flush()
                    classes_by_key[class_key] = new_classe
                    delta["classes_creees"] += 1
                elif classes_by_key[class_key].archived_at is not None:
                    classes_by_key[class_key].archived_at = None

                # 5) Élève (clé : code_eleve, repli sur identité)
                code_eleve = _s(get(row, "Code Élève"))
                prenom = _s(get(row, "Prénom"))
                identity_key = (school.id, classe, nom_eleve.upper(), (prenom or "").upper())
                existing_eleve = eleves_by_code.get(code_eleve) if code_eleve else None
                if existing_eleve is None:
                    meme_nom = eleves_by_identity.get(identity_key)
                    if meme_nom is not None and code_eleve and meme_nom.code_eleve \
                            and meme_nom.code_eleve != code_eleve:
                        # Homonyme : même nom dans la même classe, autre code. Ce
                        # n'est PAS le même élève (35 cas dans la base 2026-2027).
                        # Le code entre dans le prénom pour respecter l'unicité
                        # (école, classe, nom, prénom) et distinguer les deux
                        # élèves pour le tuteur.
                        prenom = f"{prenom or ''} ({code_eleve})".strip()
                        identity_key = (school.id, classe, nom_eleve.upper(), prenom.upper())
                        existing_eleve = eleves_by_identity.get(identity_key)
                        delta["homonymes"] += 1
                    else:
                        existing_eleve = meme_nom
                if existing_eleve is None:
                    new_eleve = Eleve(
                        id=uuid_module.uuid4(),
                        nom=nom_eleve, prenom=prenom, code_eleve=code_eleve,
                        classe=classe, genre=_s(get(row, "Sexe")),
                        statut=_s(get(row, "Statut")) or "actif",
                        statut_selection=_s(get(row, "Type")),
                        groupe_lecture=_s(get(row, "Groupe Lecture")),
                        groupe_maths=_s(get(row, "Groupe Maths")),
                        school_id=school.id,
                    )
                    db.add(new_eleve)
                    await db.flush()
                    if code_eleve:
                        eleves_by_code[code_eleve] = new_eleve
                    eleves_by_identity[identity_key] = new_eleve
                    eleves_ligne.add(new_eleve.id)
                    delta["eleves_crees"] += 1
                else:
                    # Une ligne = un élève : c'est la seule unité de comptage qui
                    # ait un sens pour l'utilisateur.
                    e = existing_eleve
                    voulu = {"school_id": school.id, "classe": classe, "nom": nom_eleve,
                             "genre": _s(get(row, "Sexe")), "statut_selection": _s(get(row, "Type")),
                             "groupe_lecture": _s(get(row, "Groupe Lecture")),
                             "groupe_maths": _s(get(row, "Groupe Maths"))}
                    if prenom is not None:
                        voulu["prenom"] = prenom
                    elif e.prenom and _nkey(e.nom) != _nkey(nom_eleve):
                        # Le fichier donne le nom complet dans « Nom » (base 2026-2027) :
                        # l'ancien prénom ferait doublon à l'affichage.
                        e.prenom = None
                    statut = _s(get(row, "Statut"))
                    if e.archived_at is not None:
                        e.archived_at = None  # cité par le fichier : restauré
                        voulu["statut"] = statut or "actif"
                        change = True
                    else:
                        change = False
                        if statut:
                            voulu["statut"] = statut
                    for attr, v in voulu.items():
                        if v is not None and getattr(e, attr) != v:
                            setattr(e, attr, v)
                            change = True
                    if change:
                        await db.flush()
                        eleves_by_identity[(e.school_id, e.classe, (e.nom or "").strip().upper(),
                                            (e.prenom or "").strip().upper())] = e
                        delta["eleves_mis_a_jour"] += 1
                    else:
                        delta["ignores"] += 1
                    eleves_ligne.add(e.id)

        except Exception as exc:
            # Ligne annulée en entier par le point de sauvegarde : rien n'est
            # compté, et les index en mémoire sont rechargés depuis la base.
            summary.lignes_en_erreur += 1
            if isinstance(exc, LigneRefusee):
                msg = str(exc)
            elif isinstance(exc, IntegrityError):
                msg = "donnée en conflit avec un enregistrement existant (doublon de code, téléphone…)."
            else:
                msg = f"erreur inattendue ({type(exc).__name__})."
            summary.erreurs.append(f"Ligne {line_num} : {msg} Ligne non importée.")
            await charger()
            continue
        summary.lignes_validees += 1
        comptes_vus.update(vus_ligne)
        ecoles_vues.update(ecoles_ligne)
        eleves_vus.update(eleves_ligne)
        for k, v in delta.items():
            setattr(summary, k, getattr(summary, k) + v)

    if nouvelle_annee and summary.lignes_validees:
        await avancer("archivage", total, total)
        await _archiver_hors_fichier(db, summary, admin, eleves_vus, codes_fichier, comptes_vus, tels_fichier)

    if eleves_vus:
        await avancer("controles", total, total)
        summary.avertissements += await _eleves_sans_tuteur(db, eleves_vus)

    if len(summary.erreurs) > 50:
        summary.erreurs_tronquees = len(summary.erreurs) - 50
        summary.erreurs = summary.erreurs[:50]
    if dry_run:
        # Aperçu : tout a été vérifié contre la base réelle, rien n'est gardé.
        await db.rollback()
    else:
        journaliser(db, acteur=admin, action="import", entity="Import global",
                    method="POST", path="/admin/import-export/import",
                    description=(f"Import global{' (nouvelle année)' if nouvelle_annee else ''} : "
                                 f"{summary.lignes_validees} ligne(s) validée(s), {summary.lignes_en_erreur} en erreur, "
                                 f"{summary.eleves_crees} élève(s) créé(s), {summary.eleves_mis_a_jour} mis à jour, "
                                 f"{summary.eleves_archives} archivé(s), {summary.comptes_archives} compte(s) archivé(s)."))
        invalidate_sync_caches_after_commit(db)
    return summary


async def _archiver_hors_fichier(db, summary: ImportSummary, admin: User, eleves_vus: set, codes_fichier: set,
                                 comptes_vus: set, tels_fichier: set) -> None:
    """Nouvelle année : archive (réversible, historique conservé — APP-15) les
    élèves, tuteurs et superviseurs que le fichier ne cite pas.

    Un élève ou un compte cité par une ligne refusée n'est pas archivé : il
    est protégé par son code élève ou son téléphone.
    """
    now = datetime.now(timezone.utc)
    eleves = (await db.execute(select(Eleve).where(Eleve.archived_at.is_(None)))).scalars().all()
    for e in eleves:
        if e.id in eleves_vus or (e.code_eleve and e.code_eleve in codes_fichier):
            continue
        e.archived_at, e.statut = now, "archive"
        summary.eleves_archives += 1
    comptes = (await db.execute(select(User).where(
        User.role.in_((UserRole.enseignant, UserRole.superviseur)), User.archived_at.is_(None),
    ))).scalars().all()
    for u in comptes:
        if u.id in comptes_vus or (u.phone and u.phone in tels_fichier):
            continue
        u.archived_at, u.status, u.tokens_valid_from = now, UserStatus.inactif, now
        summary.comptes_archives += 1
    await db.flush()
    if summary.eleves_archives or summary.comptes_archives:
        journaliser(db, acteur=admin, action="archive", entity="Import global", method="POST",
                    path="/admin/import-export/import",
                    description=(f"Nouvelle année : {summary.eleves_archives} élève(s) et "
                                 f"{summary.comptes_archives} compte(s) absents du fichier archivés."))


async def _eleves_sans_tuteur(db, eleves_vus: set) -> list[str]:
    """Élèves importés qu'aucun tuteur actif ne suit : invisibles dans l'app."""
    rows = (await db.execute(text("""
        SELECT s.name AS ecole, e.classe, COUNT(*) AS n
        FROM eleves e JOIN schools s ON s.id = e.school_id
        WHERE e.id = ANY(:ids) AND e.archived_at IS NULL AND NOT EXISTS (
            SELECT 1 FROM users u
            WHERE u.role = 'enseignant' AND u.archived_at IS NULL AND u.school_id = e.school_id
              AND lower(e.classe) = ANY(SELECT lower(c) FROM unnest(u.classes) AS c))
        GROUP BY s.name, e.classe ORDER BY s.name, e.classe
    """), {"ids": list(eleves_vus)})).all()
    return [f"{r.ecole} — {r.classe} : {r.n} élève(s) sans tuteur, invisibles dans l'app tant "
            f"qu'un tuteur n'y est pas affecté." for r in rows]


# ── Import en tâche de fond (lot L10) ────────────────────────────────────────
# Une base complète (5 000 élèves) prend plus d'une minute : le proxy (Caddy,
# read_timeout 60 s) coupait la requête et le navigateur affichait « Serveur
# injoignable ». L'admin lance donc l'import ici, reçoit aussitôt un
# identifiant, puis interroge l'avancement (barre de progression). L'état vit
# dans Redis pour que n'importe quel worker puisse répondre. Tout l'import est
# une seule transaction : interrompu, il n'enregistre rien.

logger = logging.getLogger(__name__)

_CLE_TACHE = "import_global:tache:{}"
_VERROU = "import_global:en_cours"
_DUREE_ETAT = 24 * 3600   # l'état reste consultable 24 h
_DUREE_VERROU = 180       # rafraîchi à chaque étape ; libéré si le worker meurt
_SILENCE_MAX = 120        # sans nouvelle depuis 2 min : tâche considérée interrompue
_TACHES: set[asyncio.Task] = set()  # référence forte : une tâche non référencée peut être collectée


class TacheImport(BaseModel):
    id: str
    statut: str                      # en_cours | termine | erreur | interrompu
    phase: str = "lecture"           # lecture | lignes | archivage | controles | enregistrement
    traitees: int = 0
    total: int = 0
    dry_run: bool = True
    nouvelle_annee: bool = False
    debut: float = 0
    maj: float = 0
    resultat: Optional[ImportSummary] = None
    erreur: Optional[str] = None


@router.post("/import/taches", response_model=TacheImport, status_code=202)
async def lancer_import(
    admin: AdminUser,
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    nouvelle_annee: bool = Query(False),
) -> TacheImport:
    """Lance l'import en arrière-plan ; suivre avec GET /import/taches/{id}.

    Un seul import à la fois : deux imports simultanés se marcheraient dessus
    (mêmes comptes, mêmes élèves)."""
    rows = await asyncio.to_thread(_lire_lignes, await read_upload_capped(file))
    r = await get_redis()
    tid = uuid_module.uuid4().hex
    if not await r.set(_VERROU, tid, nx=True, ex=_DUREE_VERROU):
        raise HTTPException(status_code=409, detail=(
            "Un import est déjà en cours. Attendez sa fin (quelques minutes au plus) puis réessayez."))
    maintenant = time.time()
    etat = TacheImport(id=tid, statut="en_cours", total=max(len(rows) - 1, 0), dry_run=dry_run,
                       nouvelle_annee=nouvelle_annee, debut=maintenant, maj=maintenant)
    await r.set(_CLE_TACHE.format(tid), etat.model_dump_json(), ex=_DUREE_ETAT)
    tache = asyncio.create_task(_executer_tache(etat, admin.id, rows))
    _TACHES.add(tache)
    tache.add_done_callback(_TACHES.discard)
    return etat


@router.get("/import/taches/{tache_id}", response_model=TacheImport)
async def suivre_import(tache_id: str, _: AdminUser) -> TacheImport:
    r = await get_redis()
    brut = await r.get(_CLE_TACHE.format(tache_id))
    if brut is None:
        raise HTTPException(status_code=404, detail="Import introuvable (expiré ou jamais lancé).")
    etat = TacheImport.model_validate_json(brut)
    if etat.statut == "en_cours" and time.time() - etat.maj > _SILENCE_MAX:
        # Le worker qui portait l'import s'est arrêté (redémarrage) : la
        # transaction n'a pas été validée, rien n'est enregistré.
        etat.statut = "interrompu"
        etat.erreur = "L'import a été interrompu (redémarrage du serveur). Rien n'a été enregistré : relancez-le."
    return etat


async def _executer_tache(etat: TacheImport, admin_id, rows: list[tuple]) -> None:
    r = await get_redis()
    cle = _CLE_TACHE.format(etat.id)

    async def enregistrer() -> None:
        etat.maj = time.time()
        await r.set(cle, etat.model_dump_json(), ex=_DUREE_ETAT)

    async def progression(phase: str, traitees: int, total: int) -> None:
        etat.phase, etat.traitees, etat.total = phase, traitees, total
        await enregistrer()
        await r.expire(_VERROU, _DUREE_VERROU)

    try:
        async with AsyncSessionLocal() as db:
            admin = await db.get(User, admin_id)
            resume = await executer_import(db, admin, rows, etat.dry_run, etat.nouvelle_annee, progression)
            if not etat.dry_run:
                await progression("enregistrement", etat.total, etat.total)
                await db.commit()
        if not etat.dry_run:
            await invalidate_sync_caches()
        etat.statut, etat.phase, etat.traitees, etat.resultat = "termine", "termine", etat.total, resume
    except Exception:
        logger.exception("[Import global] échec de la tâche %s", etat.id)
        etat.statut = "erreur"
        etat.erreur = "Erreur inattendue pendant l'import : rien n'a été enregistré. Réessayez ou contactez l'équipe technique."
    finally:
        try:
            await enregistrer()
            if await r.get(_VERROU) == etat.id:
                await r.delete(_VERROU)
        except Exception:
            logger.exception("[Import global] état de la tâche %s non enregistré", etat.id)
