"""Reprise de l'historique Kobo / CSV (lot L6, Partie II D10.2 et Annexe A).

Étapes (outil `scripts/reprise_historique.py`) :
1. figer      — copie des sources et empreintes SHA-256 (manifeste) ;
2. analyser   — lecture, en-têtes reconnus, codes à faire correspondre et
                 suggestions (jamais appliquées sans validation humaine) ;
3. importer   — chaque ligne source est conservée (`reprise_lignes`) avec son
                 statut : importée, quarantaine (référence non validée, donnée
                 obligatoire absente), doublon (même identifiant de soumission)
                 ou rejetée (valeur illisible). Seules les lignes validées
                 créent des rapports (`source = 'reprise'`) et des absences ;
4. reconcilier — comparaison source / importé par total, IEF, école, acteur et
                 semaine ; chaque écart a une raison comptée (quarantaine,
                 doublon, rejet). Aucune ligne n'est supprimée pour retrouver
                 un total.

Une donnée absente de la source n'est jamais inventée : `directeur_venu` reste
NULL, un compteur manquant met la ligne en quarantaine.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eleve import Eleve
from app.models.presence_eleve import PresenceEleve
from app.models.rapport_journalier import RapportJournalier
from app.models.reprise import CorrespondanceReprise, LotReprise, RepriseLigne
from app.models.school import School
from app.models.user import User, UserRole

# Correspondance des colonnes (Annexe A). Chaque cible accepte plusieurs noms
# de colonne source ; un fichier JSON peut la surcharger (--mapping).
MAPPING_DEFAUT: dict[str, dict[str, list[str]]] = {
    "rapports": {
        "submission_id": ["_uuid", "uuid", "_submission__uuid"],
        "date": ["date", "date_rapport", "today"],
        "ief": ["IEF_label", "ief_label", "ief"],
        "commune": ["commune_label", "commune"],
        "ecole": ["ecole_label", "Ecole_label", "ecole", "school"],
        "superviseur": ["Superviseur_label", "superviseur_label", "superviseur"],
        "tuteur_code": ["CodeTuteur", "code_tuteur", "tuteur"],
        "tuteur_nom": ["Tuteur_label", "tuteur_label", "nom_tuteur"],
        "nb_abs": ["nb_abs", "nb_absences"],
        "absents": ["absents", "liste_absents"],
        "semaine": ["semaine", "semaine_programme"],
        "jour": ["jour", "jour_cours", "jour_programme"],
        "presence_superviseur": ["presence_superviseur"],
        "difficultes": ["difficultes_label", "difficultes"],
        "besoin_appui": ["besoin_appui"],
        "domaines_appui": ["domaines_appui", "domaines_appui_label"],
        "commentaires": ["autres_commentaires", "commentaires"],
    },
    "absences": {
        "parent_id": ["_submission__uuid", "_parent_uuid", "_uuid_parent", "_uuid"],
        "eleve_code": ["Id_eleve", "id_eleve", "code_eleve"],
        "eleve_nom": ["NomEleve", "nom_eleve"],
        "date": ["date"],
        "tuteur_code": ["CodeTuteur", "code_tuteur", "tuteur"],
    },
    "difficultes": {
        "parent_id": ["_submission__uuid", "_parent_uuid", "_uuid"],
        "libelle": ["difficulte_label", "difficultes_label", "difficulte", "label"],
    },
    "besoins": {
        "parent_id": ["_submission__uuid", "_parent_uuid", "_uuid"],
        "domaine": ["domaines_appui_label", "domaine_label", "domaines_appui", "domaine"],
    },
}

FICHIERS_DEFAUT = {
    "rapports": "Baseglobale.csv", "absences": "ElevesAbsents.csv",
    "difficultes": "difficulte.csv", "besoins": "BaseBesoins.csv",
}

VRAI = {"1", "oui", "yes", "true", "vrai", "o", "y"}
FAUX = {"0", "non", "no", "false", "faux", "n"}


# ── Lecture ───────────────────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for bloc in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def lire_csv(path: Path) -> tuple[list[str], list[dict]]:
    brut = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texte = brut.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    echantillon = texte[:5000]
    delim = ";" if echantillon.count(";") > echantillon.count(",") else ","
    lecteur = csv.DictReader(io.StringIO(texte), delimiter=delim)
    return list(lecteur.fieldnames or []), [dict(r) for r in lecteur]


def colonne(ligne: dict, noms: list[str]) -> Optional[str]:
    for n in noms:
        if n in ligne and ligne[n] not in (None, ""):
            v = str(ligne[n]).strip()
            if v and v.upper() not in ("NA", "N/A", "NULL", "."):
                return v
    return None


def normaliser(t: Optional[str]) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def lire_date(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    v = v.strip()
    tete = re.split(r"[T ]", v, maxsplit=1)[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(tete, fmt).date()
        except ValueError:
            continue
    return None


def lire_entier(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        f = float(v.replace(",", "."))
    except ValueError:
        return None
    return int(f) if f == int(f) and f >= 0 else None


def lire_bool(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    x = normaliser(v)
    return True if x in VRAI else False if x in FAUX else None


def liste(v: Optional[str]) -> list[str]:
    if not v:
        return []
    parts = re.split(r"\s*[;|,\n]\s*|\s{2,}", v.strip())
    return [p.strip() for p in parts if p.strip()]


# ── Correspondances ───────────────────────────────────────────────────────────

async def correspondances(db: AsyncSession) -> dict[str, dict[str, uuid.UUID]]:
    out: dict[str, dict[str, uuid.UUID]] = defaultdict(dict)
    for c in (await db.execute(select(CorrespondanceReprise))).scalars():
        out[c.type][c.code_source.strip()] = c.cible_id
    return out


async def suggestions(db: AsyncSession, type_: str, codes: dict[str, str]) -> dict[str, list[dict]]:
    """Suggestions pour codes non validés : téléphone identique, puis nom normalisé.
    Ce ne sont QUE des propositions, à valider dans la table de correspondance."""
    out: dict[str, list[dict]] = {}
    if type_ in ("tuteur", "superviseur"):
        role = UserRole.enseignant if type_ == "tuteur" else UserRole.superviseur
        users = (await db.execute(select(User).where(User.role == role))).scalars().all()
        par_tel = {re.sub(r"\D", "", u.phone or "")[-9:]: u for u in users if u.phone}
        par_nom = defaultdict(list)
        for u in users:
            par_nom[normaliser(u.name)].append(u)
        for code, libelle in codes.items():
            cands = []
            tel = re.sub(r"\D", "", code)[-9:]
            if len(tel) == 9 and tel in par_tel:
                cands.append({"cible_id": str(par_tel[tel].id), "libelle": par_tel[tel].name, "raison": "téléphone identique"})
            for u in par_nom.get(normaliser(libelle), []):
                if all(c["cible_id"] != str(u.id) for c in cands):
                    cands.append({"cible_id": str(u.id), "libelle": u.name, "raison": "nom identique"})
            out[code] = cands
    elif type_ == "eleve":
        for code, libelle in codes.items():
            rows = (await db.execute(select(Eleve).where(Eleve.code_eleve == code))).scalars().all()
            out[code] = [{"cible_id": str(e.id), "libelle": f"{e.nom} {e.prenom or ''}".strip(), "raison": "code élève identique"}
                         for e in rows]
    elif type_ == "ecole":
        ecoles = (await db.execute(select(School))).scalars().all()
        for code, libelle in codes.items():
            out[code] = [{"cible_id": str(s.id), "libelle": s.name, "raison": "nom identique"}
                         for s in ecoles if normaliser(s.name) == normaliser(libelle) or str(s.code_ecole or "") == code]
    return out


# ── Analyse ───────────────────────────────────────────────────────────────────

def charger_sources(dossier: Path, mapping: dict = MAPPING_DEFAUT, fichiers: dict = FICHIERS_DEFAUT) -> dict:
    sources = {}
    for cle, nom in fichiers.items():
        p = dossier / nom
        if not p.exists():
            sources[cle] = {"fichier": nom, "present": False, "entetes": [], "lignes": []}
            continue
        entetes, lignes = lire_csv(p)
        reconnues = {cible: next((n for n in noms if n in entetes), None) for cible, noms in mapping[cle].items()}
        sources[cle] = {"fichier": nom, "present": True, "sha256": sha256(p), "octets": p.stat().st_size,
                        "entetes": entetes, "reconnues": reconnues, "lignes": lignes}
    return sources


async def analyser(db: AsyncSession, sources: dict, mapping: dict = MAPPING_DEFAUT) -> dict:
    corr = await correspondances(db)
    m = mapping["rapports"]
    tuteurs, eleves = {}, {}
    for l in sources["rapports"]["lignes"]:
        code = colonne(l, m["tuteur_code"])
        if code and code not in corr["tuteur"]:
            tuteurs[code] = colonne(l, m["tuteur_nom"]) or ""
    for l in sources["absences"]["lignes"]:
        code = colonne(l, mapping["absences"]["eleve_code"])
        if code and code not in corr["eleve"]:
            eleves[code] = colonne(l, mapping["absences"]["eleve_nom"]) or ""
    ids = [colonne(l, m["submission_id"]) for l in sources["rapports"]["lignes"]]
    sugg_t = await suggestions(db, "tuteur", tuteurs)
    sugg_e = await suggestions(db, "eleve", eleves)
    return {
        "fichiers": {k: {kk: v[kk] for kk in ("fichier", "present", "sha256", "octets", "reconnues") if kk in v}
                     | {"lignes": len(v["lignes"]), "colonnes_non_reconnues": [
                         c for c in v["entetes"] if c not in {n for noms in mapping[k].values() for n in noms}][:50]}
                     for k, v in sources.items()},
        "doublons_identifiant": sum(n - 1 for n in Counter(i for i in ids if i).values() if n > 1),
        "sans_identifiant": sum(1 for i in ids if not i),
        "a_faire_correspondre": {
            "tuteur": [{"code_source": c, "libelle_source": n, "suggestions": sugg_t.get(c, [])} for c, n in tuteurs.items()],
            "eleve": [{"code_source": c, "libelle_source": n, "suggestions": sugg_e.get(c, [])} for c, n in eleves.items()],
        },
    }


# ── Import ────────────────────────────────────────────────────────────────────

async def importer(db: AsyncSession, lot: LotReprise, sources: dict, mapping: dict = MAPPING_DEFAUT) -> dict:
    corr = await correspondances(db)
    m = mapping["rapports"]
    deja = set((await db.execute(select(RapportJournalier.source_submission_id).where(
        RapportJournalier.source == "reprise", RapportJournalier.source_submission_id.is_not(None)))).scalars().all())
    tuteurs = {u.id: u for u in (await db.execute(select(User).where(User.role == UserRole.enseignant))).scalars()}

    # Répétitions (difficultés, besoins) groupées par soumission parente.
    diffs, besoins = defaultdict(list), defaultdict(list)
    for i, l in enumerate(sources["difficultes"]["lignes"], start=2):
        pid = colonne(l, mapping["difficultes"]["parent_id"])
        lib = colonne(l, mapping["difficultes"]["libelle"])
        if pid and lib:
            diffs[pid].append(lib)
    for l in sources["besoins"]["lignes"]:
        pid = colonne(l, mapping["besoins"]["parent_id"])
        dom = colonne(l, mapping["besoins"]["domaine"])
        if pid and dom:
            besoins[pid].append(dom)

    compte = Counter()
    vus: set[str] = set()
    importes: dict[str, RapportJournalier] = {}

    def ligne(fichier, n, sid, donnees, statut, motif=None, cible_type=None, cible_id=None, parent=None):
        compte[f"{fichier}:{statut}"] += 1
        db.add(RepriseLigne(lot_id=lot.id, fichier=fichier, numero_ligne=n, source_submission_id=sid,
                            source_parent_id=parent, donnees=donnees, statut=statut, motif=motif,
                            cible_type=cible_type, cible_id=cible_id))

    for n, l in enumerate(sources["rapports"]["lignes"], start=2):
        f = sources["rapports"]["fichier"]
        sid = colonne(l, m["submission_id"])
        if not sid:
            ligne(f, n, None, l, "rejetee", "identifiant de soumission absent")
            continue
        if sid in vus or sid in deja:
            ligne(f, n, sid, l, "doublon", "identifiant déjà importé" if sid in deja else "identifiant répété dans le fichier")
            continue
        vus.add(sid)
        code = colonne(l, m["tuteur_code"])
        tid = corr["tuteur"].get(code or "")
        d = lire_date(colonne(l, m["date"]))
        semaine, jour = lire_entier(colonne(l, m["semaine"])), lire_entier(colonne(l, m["jour"]))
        nb_abs = lire_entier(colonne(l, m["nb_abs"]))
        motifs = []
        if not tid or tid not in tuteurs:
            motifs.append(f"tuteur « {code} » sans correspondance validée")
        if d is None:
            motifs.append("date illisible")
        if not semaine or not jour:
            motifs.append("semaine ou jour manquant")
        if nb_abs is None:
            motifs.append("nombre d'absences non renseigné")
        if motifs:
            ligne(f, n, sid, l, "rejetee" if motifs == ["date illisible"] else "quarantaine", " ; ".join(motifs))
            continue
        tuteur = tuteurs[tid]
        dif = diffs.get(sid) or liste(colonne(l, m["difficultes"]))
        dom = besoins.get(sid) or liste(colonne(l, m["domaines_appui"]))
        r = RapportJournalier(
            teacher_id=tuteur.id, date_rapport=d, ief=colonne(l, m["ief"]) or "", commune=colonne(l, m["commune"]) or "",
            ecole=colonne(l, m["ecole"]) or "", superviseur=colonne(l, m["superviseur"]) or "",
            nom_tuteur=colonne(l, m["tuteur_nom"]) or tuteur.name, nb_absences=nb_abs,
            absents=colonne(l, m["absents"]), semaine=semaine, jour_cours=min(jour, 7),
            difficultes=json.dumps(dif or ["Aucune"], ensure_ascii=False), directeur_venu=None,
            besoin_appui=bool(lire_bool(colonne(l, m["besoin_appui"])) or dom),
            domaines_appui=json.dumps(dom, ensure_ascii=False) if dom else None,
            commentaires=colonne(l, m["commentaires"]), has_observations=bool(colonne(l, m["commentaires"])),
            soumis_en_offline=False, source="reprise", source_submission_id=sid, lot_reprise_id=lot.id,
            session_id=lot.session_id, ecole_id=tuteur.school_id,
        )
        db.add(r)
        await db.flush()
        importes[sid] = r
        ligne(f, n, sid, l, "importee", cible_type="rapport_journalier", cible_id=r.id)

    # Absences individuelles (repérées par la soumission parente ou la date + tuteur).
    for n, l in enumerate(sources["absences"]["lignes"], start=2):
        f = sources["absences"]["fichier"]
        pid = colonne(l, mapping["absences"]["parent_id"])
        code = colonne(l, mapping["absences"]["eleve_code"])
        eid = corr["eleve"].get(code or "")
        parent = importes.get(pid or "")
        d = lire_date(colonne(l, mapping["absences"]["date"])) or (parent.date_rapport if parent else None)
        if not eid:
            ligne(f, n, None, l, "quarantaine", f"élève « {code} » sans correspondance validée", parent=pid)
            continue
        if d is None:
            ligne(f, n, None, l, "rejetee", "date illisible", parent=pid)
            continue
        existe = (await db.execute(select(PresenceEleve.id).where(
            PresenceEleve.eleve_id == eid, PresenceEleve.date_jour == d))).first()
        if existe:
            ligne(f, n, None, l, "doublon", "absence déjà enregistrée pour cet élève à cette date", parent=pid)
            continue
        p = PresenceEleve(eleve_id=eid, date_jour=d, session_id=lot.session_id,
                          teacher_id=parent.teacher_id if parent else None, statut="absent", source="reprise")
        db.add(p)
        await db.flush()
        ligne(f, n, None, l, "importee", cible_type="presence_eleve", cible_id=p.id, parent=pid)

    for cle in ("difficultes", "besoins"):
        f = sources[cle]["fichier"]
        champ = "libelle" if cle == "difficultes" else "domaine"
        for n, l in enumerate(sources[cle]["lignes"], start=2):
            pid = colonne(l, mapping[cle]["parent_id"])
            val = colonne(l, mapping[cle][champ])
            if not pid or not val:
                ligne(f, n, None, l, "rejetee", "identifiant parent ou valeur absent", parent=pid)
            elif pid in importes:
                ligne(f, n, None, l, "importee", cible_type="rapport_journalier", cible_id=importes[pid].id, parent=pid)
            else:
                ligne(f, n, None, l, "quarantaine", "rapport parent non importé", parent=pid)

    lot.compteurs = dict(compte)
    lot.statut = "importe"
    lot.importe_le = datetime.now(timezone.utc)
    await db.flush()
    lot.reconciliation = await reconcilier(db, lot, sources, mapping)
    await db.flush()
    return {"compteurs": lot.compteurs, "reconciliation": lot.reconciliation}


# ── Réconciliation ────────────────────────────────────────────────────────────

async def reconcilier(db: AsyncSession, lot: LotReprise, sources: dict, mapping: dict = MAPPING_DEFAUT) -> dict:
    """Source vs importé par niveau ; l'écart est expliqué par statut de ligne."""
    m = mapping["rapports"]
    lignes = (await db.execute(select(RepriseLigne).where(RepriseLigne.lot_id == lot.id))).scalars().all()
    statut_par_pos = {(x.fichier, x.numero_ligne): x.statut for x in lignes}
    f = sources["rapports"]["fichier"]
    niveaux = {"total": lambda l: "total", "ief": lambda l: colonne(l, m["ief"]) or "(vide)",
               "ecole": lambda l: colonne(l, m["ecole"]) or "(vide)",
               "acteur": lambda l: colonne(l, m["tuteur_code"]) or "(vide)",
               "semaine": lambda l: colonne(l, m["semaine"]) or "(vide)"}
    out = {}
    for niveau, cle in niveaux.items():
        agg: dict[str, Counter] = defaultdict(Counter)
        for n, l in enumerate(sources["rapports"]["lignes"], start=2):
            k = cle(l)
            agg[k]["source"] += 1
            agg[k][statut_par_pos.get((f, n), "non_traitee")] += 1
        out[niveau] = [{"cle": k, **v, "ecart": v["source"] - v.get("importee", 0),
                        "ecart_explique": v["source"] - v.get("importee", 0) == sum(
                            v.get(s, 0) for s in ("quarantaine", "doublon", "rejetee"))}
                       for k, v in sorted(agg.items())]
    autres = {}
    for cle in ("absences", "difficultes", "besoins"):
        c = Counter(x.statut for x in lignes if x.fichier == sources[cle]["fichier"])
        autres[cle] = {"source": len(sources[cle]["lignes"]), **c}
    return {"rapports": out, "repetitions": autres,
            "toutes_lignes_expliquees": all(r["ecart_explique"] for r in out["total"])}
