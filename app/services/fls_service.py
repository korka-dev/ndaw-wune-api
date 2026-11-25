"""Écoles avec / sans FLS (français langue seconde) — lot L9.

- Un créneau d'emploi du temps peut être réservé aux écoles avec FLS
  (`planning_segments.reserve_fls`). Il est masqué aux tuteurs d'une école
  marquée sans FLS (`schools.fls = false`) ; une école non renseignée
  (`NULL`) voit tout l'emploi du temps, comme avant.
- La répartition s'importe depuis le fichier de l'équipe programme
  (« Extraction_IA_IEF_ecoles_FLS.xlsx ») : colonnes IA, IEF, « Écoles avec
  FLS », « Écoles sans FLS », une feuille récapitulative et une par IEF.
  Rien n'est deviné : une école introuvable ou ambiguë est signalée, avec une
  suggestion de nom proche, et n'est pas modifiée.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.upload_utils import iter_rows_capped, load_workbook_safe
from app.models.planning import PlanningSegment
from app.models.school import School

_FLS_MOT = re.compile(r"(^|[^a-z])fls([^a-z]|$)")


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c))


def est_creneau_fls(matiere: Optional[str]) -> bool:
    """« Français langue seconde », « FLS », « Leçon de FLS »… (détection à la création et à l'import)."""
    m = _sans_accents(matiere or "").lower()
    return "langue seconde" in m or bool(_FLS_MOT.search(m))


def condition_visibilite(school: Optional[School]):
    """Condition SQL sur les créneaux visibles par un tuteur de cette école."""
    if school is not None and school.fls is False:
        return PlanningSegment.reserve_fls.is_(False)
    return None


# ── Import de la répartition ──────────────────────────────────────────────────

def cle_nom(nom: Optional[str]) -> str:
    """Nom comparable : sans accents, casse, ponctuation ni espaces multiples."""
    t = _sans_accents(str(nom or "")).upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    # « SERERE2 » et « SERERE 2 », « 1ER » et « 1 ER » se comparent pareil.
    t = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", t)
    return " ".join(t.split())


def cle_ief(ief: Optional[str]) -> str:
    """« IEF Kaolack-commune » et « Kaolack commune » se comparent pareil."""
    return re.sub(r"^IEF ", "", cle_nom(ief))


# Préfixes d'établissement ignorés pour le rapprochement (« EE NDECK » = « NDECK »).
_PREFIXES = ("ECOLE ELEMENTAIRE ", "ECOLE ", "E E ", "EE ", "EFA ", "EP ")


def _sans_prefixe(cle: str) -> str:
    for p in _PREFIXES:
        if cle.startswith(p):
            return cle[len(p):]
    return cle


def _numeros(cle: str) -> list[str]:
    return re.findall(r"\d+", cle)


@dataclass
class LigneFls:
    ia: Optional[str]
    ief: Optional[str]
    nom: str
    fls: bool
    feuilles: set[str] = field(default_factory=set)


def lire_repartition(content: bytes) -> tuple[list[LigneFls], list[dict]]:
    """Lit toutes les feuilles au format IA / IEF / avec FLS / sans FLS.

    Retourne les écoles (dédoublonnées entre la feuille récapitulative et les
    feuilles par IEF) et les erreurs (feuille ou ligne illisible, école classée
    à la fois avec et sans FLS).
    """
    wb = load_workbook_safe(content)
    ecoles: dict[tuple[str, str], LigneFls] = {}
    erreurs: list[dict] = []
    feuilles_lues = 0
    for ws in wb.worksheets:
        lignes = iter_rows_capped(ws)
        entete = next(lignes, None)
        if not entete:
            continue
        cols = {cle_nom(v): i for i, v in enumerate(entete) if v is not None}
        i_avec, i_sans = cols.get("ECOLES AVEC FLS"), cols.get("ECOLES SANS FLS")
        if i_avec is None or i_sans is None:
            continue
        feuilles_lues += 1
        i_ia, i_ief = cols.get("IA"), cols.get("IEF")
        for n, row in enumerate(lignes, start=2):
            def val(i):
                v = row[i] if i is not None and i < len(row) else None
                return str(v).strip() if v is not None and str(v).strip() else None
            avec, sans = val(i_avec), val(i_sans)
            if not avec and not sans:
                continue
            if avec and sans:
                erreurs.append({"feuille": ws.title, "ligne": n,
                                "erreur": "Deux écoles sur la même ligne (avec et sans FLS) : ligne ignorée."})
                continue
            nom, fls = (avec, True) if avec else (sans, False)
            cle = (cle_ief(val(i_ief)), cle_nom(nom))
            deja = ecoles.get(cle)
            if deja is None:
                ecoles[cle] = LigneFls(ia=val(i_ia), ief=val(i_ief), nom=nom, fls=fls, feuilles={ws.title})
            elif deja.fls != fls:
                erreurs.append({"feuille": ws.title, "ligne": n,
                                "erreur": f"« {nom} » est classée avec FLS dans une feuille et sans FLS dans une autre : ignorée."})
                deja.fls = None  # type: ignore[assignment]  — conflit : exclu
            else:
                deja.feuilles.add(ws.title)
    if not feuilles_lues:
        raise HTTPException(status_code=422, detail=(
            "Fichier non reconnu : aucune feuille avec les colonnes « Écoles avec FLS » et « Écoles sans FLS »."
        ))
    return [e for e in ecoles.values() if e.fls is not None], erreurs


async def rapprocher(db: AsyncSession, lignes: list[LigneFls],
                     associations: Optional[dict[str, str]] = None) -> dict:
    """Associe chaque ligne du fichier à une école de la base (sans rien modifier).

    `associations` : choix explicites de l'admin, {nom dans le fichier: id
    d'école}, pour une école introuvable (suggestion acceptée) ou ambiguë.
    """
    ecoles = (await db.execute(select(School).where(School.archived_at.is_(None)))).scalars().all()
    par_id = {str(s.id): s for s in ecoles}
    par_nom: dict[str, list[School]] = {}
    par_nom_court: dict[str, list[School]] = {}
    for s in ecoles:
        par_nom.setdefault(cle_nom(s.name), []).append(s)
        par_nom_court.setdefault(_sans_prefixe(cle_nom(s.name)), []).append(s)
    choix = {cle_nom(k): v for k, v in (associations or {}).items()}

    trouvees, introuvables, ambigues = [], [], []
    vues: dict = {}
    for ligne in lignes:
        cle = cle_nom(ligne.nom)
        base = {"nom_fichier": ligne.nom, "ief_fichier": ligne.ief, "fls": ligne.fls}
        associee = False
        if cle in choix:
            ecole = par_id.get(str(choix[cle]))
            if ecole is None:
                introuvables.append({**base, "suggestion": None, "suggestion_id": None,
                                     "raison": "école choisie introuvable ou archivée"})
                continue
            candidats, associee = [ecole], True
        else:
            candidats = par_nom.get(cle) or par_nom_court.get(_sans_prefixe(cle)) or []
        if len(candidats) > 1:
            # Départage par l'IEF quand l'école porte son IEF.
            meme_ief = [s for s in candidats if s.region and cle_ief(s.region) == cle_ief(ligne.ief)]
            candidats = meme_ief or candidats
        if not candidats:
            sugg = _suggestion(ligne, ecoles)
            introuvables.append({**base, "suggestion": sugg.name if sugg else None,
                                 "suggestion_id": str(sugg.id) if sugg else None,
                                 "suggestion_ief": sugg.region if sugg else None})
            continue
        if len(candidats) > 1:
            ambigues.append({**base, "ecoles": [{"id": str(s.id), "nom": s.name, "ief": s.region} for s in candidats]})
            continue
        s = candidats[0]
        if s.id in vues:
            ambigues.append({**base, "ecoles": [{"id": str(s.id), "nom": s.name, "ief": s.region}],
                             "raison": f"même école que « {vues[s.id]} » dans le fichier"})
            continue
        vues[s.id] = ligne.nom
        trouvees.append({
            **base, "ecole": s, "id": str(s.id), "nom": s.name, "ief": s.region, "associee": associee,
            "fls_actuel": s.fls, "change": s.fls is not ligne.fls,
            "ief_differente": bool(s.region) and cle_ief(s.region) != cle_ief(ligne.ief),
            "ief_manquante": not (s.region or "").strip(),
        })
    return {"trouvees": trouvees, "introuvables": introuvables, "ambigues": ambigues}


def _suggestion(ligne: LigneFls, ecoles) -> Optional[School]:
    """École au nom proche, à proposer (jamais appliquée sans accord de l'admin).

    Cherchée d'abord dans la même IEF, sans les préfixes EE / EFA ; jamais une
    école d'un autre numéro (« NDIOUM 3 » n'est pas « NDIOUM 2 »).
    """
    cle = _sans_prefixe(cle_nom(ligne.nom))
    meme_ief = [s for s in ecoles if s.region and cle_ief(s.region) == cle_ief(ligne.ief)]
    for groupe, seuil in ((meme_ief, 0.82), (ecoles, 0.88)):
        noms: dict[str, School] = {}
        for s in groupe:
            noms.setdefault(_sans_prefixe(cle_nom(s.name)), s)
        proches = [p for p in difflib.get_close_matches(cle, list(noms), n=5, cutoff=seuil)
                   if _numeros(p) == _numeros(cle)]
        if proches:
            return noms[proches[0]]
    return None


def resume_public(r: dict) -> dict:
    return {
        "trouvees": [{k: v for k, v in t.items() if k != "ecole"} for t in r["trouvees"]],
        "introuvables": r["introuvables"],
        "ambigues": r["ambigues"],
    }


async def ecoles_sans_type(db: AsyncSession) -> int:
    return len((await db.execute(
        select(School.id).where(School.archived_at.is_(None), School.fls.is_(None))
    )).all())


def filtre_type_ecole(type_ecole: Optional[str]):
    """Aperçu admin du planning : « sans_fls » masque les créneaux réservés FLS."""
    if type_ecole in (None, "", "tous", "fls"):
        return None
    if type_ecole == "sans_fls":
        return PlanningSegment.reserve_fls.is_(False)
    raise HTTPException(status_code=422, detail="type_ecole : tous, fls ou sans_fls.")

