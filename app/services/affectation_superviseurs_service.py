"""
Affectation superviseur → tuteurs depuis l'onglet « Classes » de la base
acteurs/élèves 2026-2027 (Base_acteurs_eleves_NWN_…xlsx).

Chaque ligne de l'onglet est une classe NWN avec son « Enseignant proposé » et
son « Superviseur proposé » (nom + téléphone). Les comptes sont retrouvés par
numéro de téléphone (9 derniers chiffres, indicatif ignoré) ; l'affectation est
écrite dans `User.classes` du superviseur, comme le bouton « Gérer » de
Gestion Superviseurs (POST /admin/superviseurs/{id}/assign-teachers).

Par défaut on AJOUTE les tuteurs du fichier aux affectations existantes ;
`remplacer=True` remplace la liste par celle du fichier.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import openpyxl
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole

ONGLET = "Classes"
COLONNES = {
    "classe": "Code Classe NWN",
    "code_ecole": "Code École",
    "ecole": "École",
    "tuteur": "Enseignant proposé",
    "tel_tuteur": "Téléphone enseignant",
    "superviseur": "Superviseur proposé",
    "tel_superviseur": "Téléphone superviseur",
}


def tel9(v: object) -> str:
    """9 derniers chiffres du numéro (« +221 77 123 45 67 » → « 771234567 »)."""
    chiffres = re.sub(r"\D", "", str(v or ""))
    return chiffres[-9:] if len(chiffres) >= 9 else ""


def _txt(v: object) -> str:
    return " ".join(str(v or "").split())


@dataclass
class Ligne:
    classe: str
    code_ecole: str
    ecole: str
    tuteur: str
    tel_tuteur: str
    superviseur: str
    tel_superviseur: str


def lire(contenu: bytes) -> list[Ligne]:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="Fichier illisible : un classeur Excel (.xlsx) est attendu.")
    if ONGLET not in wb.sheetnames:
        raise HTTPException(status_code=422, detail=f"Onglet « {ONGLET} » introuvable dans le fichier.")
    lignes = wb[ONGLET].iter_rows(values_only=True)
    entete = [_txt(c) for c in next(lignes, [])]
    manquantes = [c for c in COLONNES.values() if c not in entete]
    if manquantes:
        raise HTTPException(status_code=422, detail=f"Colonnes manquantes dans « {ONGLET} » : {', '.join(manquantes)}.")
    ix = {k: entete.index(c) for k, c in COLONNES.items()}
    out: list[Ligne] = []
    for r in lignes:
        if not r or not _txt(r[ix["classe"]]):
            continue
        out.append(Ligne(
            classe=_txt(r[ix["classe"]]),
            code_ecole=_txt(r[ix["code_ecole"]]),
            ecole=_txt(r[ix["ecole"]]),
            tuteur=_txt(r[ix["tuteur"]]),
            tel_tuteur=tel9(r[ix["tel_tuteur"]]),
            superviseur=_txt(r[ix["superviseur"]]),
            tel_superviseur=tel9(r[ix["tel_superviseur"]]),
        ))
    return out


def _est_superviseur(u: User) -> bool:
    return u.role == UserRole.superviseur or (u.role == UserRole.coordonnateur and u.title != "evaluateur")


async def traiter(db: AsyncSession, lignes: list[Ligne], remplacer: bool = False) -> dict:
    """Calcule et applique (dans la session, sans commit) les affectations.

    Retourne un rapport : affectés, inchangés, superviseurs / tuteurs
    introuvables, classes incomplètes, tuteurs déjà suivis par un autre
    superviseur.
    """
    comptes = (await db.execute(
        select(User).where(
            User.archived_at.is_(None),
            User.phone.is_not(None),
            or_(User.role == UserRole.enseignant, User.role == UserRole.superviseur,
                User.role == UserRole.coordonnateur),
        )
    )).scalars().all()
    tuteurs: dict[str, User] = {}
    superviseurs: dict[str, User] = {}
    for u in comptes:
        t = tel9(u.phone)
        if not t:
            continue
        if u.role == UserRole.enseignant:
            tuteurs[t] = u
        elif _est_superviseur(u):
            superviseurs[t] = u

    rapport: dict[str, list] = {
        "affectes": [], "inchanges": [], "superviseurs_introuvables": [],
        "tuteurs_introuvables": [], "classes_incompletes": [], "deja_suivis_ailleurs": [],
    }

    # Regroupement du fichier par superviseur.
    voulu: dict[str, dict] = {}
    for l in lignes:
        if not l.tel_superviseur or not l.tel_tuteur:
            rapport["classes_incompletes"].append({"classe": l.classe, "ecole": l.ecole})
            continue
        g = voulu.setdefault(l.tel_superviseur, {"nom": l.superviseur, "ecole": l.ecole, "tuteurs": []})
        g["tuteurs"].append(l)

    # Qui suit déjà quel tuteur (pour signaler les changements de superviseur).
    suivi_par: dict[str, User] = {}
    for s in superviseurs.values():
        for tid in s.classes or []:
            suivi_par.setdefault(str(tid), s)

    for tel_sup, g in voulu.items():
        sup = superviseurs.get(tel_sup)
        if sup is None:
            rapport["superviseurs_introuvables"].append(
                {"nom": g["nom"], "telephone": tel_sup, "ecole": g["ecole"], "nb_tuteurs": len(g["tuteurs"])})
            continue
        ids: list[str] = []
        noms: list[str] = []
        for l in g["tuteurs"]:
            t = tuteurs.get(l.tel_tuteur)
            if t is None:
                rapport["tuteurs_introuvables"].append(
                    {"nom": l.tuteur, "telephone": l.tel_tuteur, "ecole": l.ecole, "superviseur": sup.name})
                continue
            autre = suivi_par.get(str(t.id))
            if autre is not None and autre.id != sup.id:
                rapport["deja_suivis_ailleurs"].append(
                    {"tuteur": t.name, "superviseur_actuel": autre.name, "superviseur_fichier": sup.name})
            if str(t.id) not in ids:
                ids.append(str(t.id))
                noms.append(t.name)
        if not ids:
            continue
        avant = [str(x) for x in (sup.classes or [])]
        apres = ids if remplacer else avant + [i for i in ids if i not in avant]
        ligne = {"superviseur": sup.name, "telephone": tel_sup, "tuteurs": noms,
                 "avant": len(avant), "apres": len(apres)}
        if apres == avant:
            rapport["inchanges"].append(ligne)
        else:
            sup.classes = apres
            rapport["affectes"].append(ligne)
    await db.flush()
    return rapport
