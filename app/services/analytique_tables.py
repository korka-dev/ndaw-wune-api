"""Tables analytiques (rapport V2, lot L4 — Partie II, D4/D5).

Une seule définition des jeux de données sert à la fois :
- l'archive CSV analytique (pack zippé, D6.1) ;
- l'API analytique paginée et incrémentale (D6.1, D6.5) ;
- les indicateurs (D7, APP-36), calculés à partir des MÊMES lignes que le
  pack : un indicateur se recalcule à l'identique depuis les fichiers (REC16).

Principes appliqués (D4) :
- une table = une unité de ligne, une clé, des relations explicites ;
- identifiants techniques (UUID) immuables ; jamais de clé construite sur un
  nom. Les clés dérivées (absence, signalement…) sont des UUID v5
  déterministes : identiques d'un export à l'autre ;
- la valeur manquante reste vide, jamais remplacée par zéro ;
- dates d'activité en AAAA-MM-JJ, horodatages ISO 8601 avec fuseau, jour de
  suivi interprété en Africa/Dakar ; codes et identifiants exportés en texte ;
- un module non collecté par l'application est livré vide avec son en-tête et
  signalé « non_collecte » dans le manifeste (aucune donnée inventée).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.eleve import Eleve
from app.models.eleve_remplacement import EleveRemplacement
from app.models.evaluation_competence import EvaluationCompetence
from app.models.evaluation_eleve import EvaluationEleve
from app.models.evaluation_resultat import EvaluationResultat, EvaluationResultatHistorique
from app.models.evaluation_sujet import EvaluationSujet
from app.models.evaluation_tirage import EvaluationTirage
from app.models.planning import PlanningSegment
from app.models.rapport_difficulte import RapportDifficulte
from app.models.rapport_difficulte_resolution import RapportDifficulteResolution
from app.models.rapport_journalier import RapportJournalier
from app.models.rapport_question import RapportQuestion, RapportQuestionVersion
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.seance import Seance
from app.models.session import ProgramSession, TeacherSession
from app.models.supervisor_presence import SupervisorPresenceCheck
from app.models.user import User, UserRole

SCHEMA_VERSION = "1.0"
FUSEAU = "Africa/Dakar"
TZ = ZoneInfo(FUSEAU)
NS = uuid.UUID("6f1d3c52-8a4e-4c7b-9f0e-2b5a7d9e1c34")  # espace des clés dérivées

# Correspondance des résultats des deux sources d'évaluation (APP-36) : le
# code source est TOUJOURS conservé ; l'harmonisation est une colonne dérivée.
RESULTAT_HARMONISE = {
    "acquis": "acquis", "en_cours": "en_cours", "a_aider": "a_aider",
    "reussi": "acquis", "intermediaire": "en_cours", "pas_reussi": "a_aider",
}
AUCUNE = "AUCUNE"
AUTRE = "AUTRE"


# ── Filtres ──────────────────────────────────────────────────────────────────

@dataclass
class Filtres:
    """Filtres communs (D6.2). La période porte sur la date d'activité ; la
    réception (`recu_*`) se filtre à part pour isoler les données tardives."""
    campagne_id: Optional[uuid.UUID] = None
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    recu_depuis: Optional[datetime] = None
    recu_jusqua: Optional[datetime] = None
    ief: Optional[str] = None
    commune: Optional[str] = None
    ecole_id: Optional[uuid.UUID] = None
    tuteur_id: Optional[uuid.UUID] = None
    superviseur_id: Optional[uuid.UUID] = None
    statut: str = "tous"            # tous | valides | soumis | corrige | annule
    nominatif: bool = False

    def en_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, (date, datetime)) else str(v) if isinstance(v, uuid.UUID) else v)
                for k, v in self.__dict__.items() if v not in (None, "")}

    def geo(self) -> bool:
        return any((self.ief, self.commune, self.ecole_id, self.tuteur_id, self.superviseur_id))


# ── Description des colonnes et des tables ───────────────────────────────────

@dataclass
class Col:
    nom: str
    type: str                 # identifiant | texte | code | entier | decimal | booleen | date | horodatage
    definition: str
    nature: str = "saisi"     # saisi | derive | rapproche | corrige | technique | reference
    valeurs: str = ""
    nominatif: bool = False   # exportée seulement en mode nominatif


@dataclass
class Table:
    nom: str
    unite: str
    cle: str
    colonnes: list[Col]
    builder: Optional[Callable[["Contexte"], Awaitable[list[dict]]]] = None
    statut: str = "disponible"   # disponible | non_collecte
    note: str = ""
    maj: Optional[str] = "updated_at"   # colonne de reprise incrémentale

    def colonnes_pour(self, nominatif: bool) -> list[Col]:
        return [c for c in self.colonnes if nominatif or not c.nominatif]


@dataclass
class Anomalie:
    table_source: str
    ligne_id: str
    controle: str
    detail: str
    gravite: str = "a_verifier"   # a_verifier | quarantaine | information


@dataclass
class Contexte:
    db: AsyncSession
    f: Filtres
    anomalies: list[Anomalie] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)

    def eleve(self, eid: Optional[uuid.UUID | str]) -> Optional[str]:
        """Identifiant élève exporté : UUID en mode nominatif, identifiant
        opaque stable (HMAC) sinon (D11 : jointures sans publier les noms)."""
        if eid is None or eid == "":
            return None
        if self.f.nominatif:
            return str(eid)
        mac = hmac.new(settings.SECRET_KEY.encode(), f"eleve:{eid}".encode(), hashlib.sha256).hexdigest()
        return "E" + mac[:20]

    def signaler(self, table: str, ligne: Any, controle: str, detail: str, gravite: str = "a_verifier") -> None:
        self.anomalies.append(Anomalie(table, str(ligne), controle, detail, gravite))

    async def memo(self, cle: str, calcul: Callable[[], Awaitable[Any]]) -> Any:
        if cle not in self.cache:
            self.cache[cle] = await calcul()
        return self.cache[cle]


def cle_derivee(*parts: Any) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def jour_dakar(dt: Optional[datetime]) -> Optional[date]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).date()


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").strip().split()).lower()


def _liste_json(v: Optional[str]) -> Optional[list]:
    if v is None or str(v).strip() == "":
        return None
    try:
        val = json.loads(v)
        return val if isinstance(val, list) else None
    except (ValueError, TypeError):
        return None


# ── Périmètre géographique / hiérarchique (D6.2) ─────────────────────────────

async def tuteurs_perimetre(ctx: Contexte) -> Optional[set[uuid.UUID]]:
    """Tuteurs couverts par les filtres école / IEF / commune / tuteur /
    superviseur ; None si aucun de ces filtres n'est actif. Les comptes
    archivés restent inclus (analyse historique)."""
    f = ctx.f
    if not f.geo():
        return None

    async def calcul():
        q = select(User.id).outerjoin(School, School.id == User.school_id).where(User.role == UserRole.enseignant)
        if f.ecole_id:
            q = q.where(User.school_id == f.ecole_id)
        if f.ief:
            q = q.where(func.lower(School.region) == f.ief.strip().lower())
        if f.commune:
            q = q.where(func.lower(School.city) == f.commune.strip().lower())
        if f.tuteur_id:
            q = q.where(User.id == f.tuteur_id)
        ids = set((await ctx.db.execute(q)).scalars().all())
        if f.superviseur_id:
            sup = await ctx.db.get(User, f.superviseur_id)
            assignes: set[uuid.UUID] = set()
            for raw in (sup.classes or []) if sup else []:
                try:
                    assignes.add(uuid.UUID(str(raw)))
                except ValueError:
                    continue
            ids &= assignes
        return ids

    return await ctx.memo("tuteurs_perimetre", calcul)


# ── Référentiels ─────────────────────────────────────────────────────────────

async def b_campagnes(ctx: Contexte) -> list[dict]:
    q = select(ProgramSession)
    if ctx.f.campagne_id:
        q = q.where(ProgramSession.id == ctx.f.campagne_id)
    return [{
        "campagne_id": s.id, "nom": s.name, "type_campagne": None,
        "date_debut": s.date_debut, "date_fin": s.date_fin,
        "statut": s.status.value if hasattr(s.status, "value") else s.status,
        "archive_le": s.archived_at, "created_at": s.created_at, "updated_at": s.updated_at,
    } for s in (await ctx.db.execute(q.order_by(ProgramSession.date_debut, ProgramSession.id))).scalars()]


async def b_ecoles(ctx: Contexte) -> list[dict]:
    q = select(School)
    if ctx.f.ecole_id:
        q = q.where(School.id == ctx.f.ecole_id)
    if ctx.f.ief:
        q = q.where(func.lower(School.region) == ctx.f.ief.strip().lower())
    if ctx.f.commune:
        q = q.where(func.lower(School.city) == ctx.f.commune.strip().lower())
    return [{
        "ecole_id": s.id, "code_ecole": None if s.code_ecole is None else str(s.code_ecole),
        "nom": s.name, "ief_libelle": s.region, "commune_libelle": s.city, "langue": s.langue,
        "archive_le": s.archived_at, "created_at": s.created_at, "updated_at": s.updated_at,
    } for s in (await ctx.db.execute(q.order_by(School.name, School.id))).scalars()]


async def _classes_index(ctx: Contexte) -> dict[tuple, uuid.UUID]:
    async def calcul():
        rows = (await ctx.db.execute(select(SchoolClasse.id, SchoolClasse.school_id, SchoolClasse.name))).all()
        return {(r.school_id, _norm(r.name)): r.id for r in rows}
    return await ctx.memo("classes_index", calcul)


async def b_classes(ctx: Contexte) -> list[dict]:
    q = select(SchoolClasse)
    if ctx.f.ecole_id:
        q = q.where(SchoolClasse.school_id == ctx.f.ecole_id)
    return [{
        "classe_id": c.id, "ecole_id": c.school_id, "campagne_id": None, "nom": c.name, "niveau": c.niveau,
        "effectif_declare": c.effectif, "archive_le": c.archived_at,
        "created_at": c.created_at, "updated_at": c.updated_at,
    } for c in (await ctx.db.execute(q.order_by(SchoolClasse.school_id, SchoolClasse.name, SchoolClasse.id))).scalars()]


async def b_acteurs(ctx: Contexte) -> list[dict]:
    q = select(User).where(User.role.in_([UserRole.enseignant, UserRole.superviseur]))
    perim = await tuteurs_perimetre(ctx)
    if perim is not None:
        q = q.where(or_(User.id.in_(perim), User.role == UserRole.superviseur))
    return [{
        "acteur_id": u.id, "role_acteur": u.role.value, "statut": u.status.value, "ecole_id": u.school_id,
        "archive_le": u.archived_at, "created_at": u.created_at, "updated_at": u.updated_at,
        "nom": u.name, "groupe_recherche": u.groupe_recherche.value if u.groupe_recherche else None,
    } for u in (await ctx.db.execute(q.order_by(User.role, User.id))).scalars()]


async def b_affectations(ctx: Contexte) -> list[dict]:
    """Affectations connues. Limite documentée : l'application ne date pas
    encore les affectations de supervision (décision ARED, Annexe D)."""
    rows: list[dict] = []
    q = select(TeacherSession)
    if ctx.f.campagne_id:
        q = q.where(TeacherSession.session_id == ctx.f.campagne_id)
    perim = await tuteurs_perimetre(ctx)
    for ts in (await ctx.db.execute(q)).scalars():
        if perim is not None and ts.teacher_id not in perim:
            continue
        rows.append({
            "affectation_id": cle_derivee("tuteur_campagne", ts.teacher_id, ts.session_id),
            "type_affectation": "tuteur_campagne", "acteur_id": ts.teacher_id, "campagne_id": ts.session_id,
            "tuteur_id": ts.teacher_id, "superviseur_id": None,
            "date_debut": jour_dakar(ts.created_at), "date_fin": None, "datee": 1,
            "created_at": ts.created_at, "updated_at": ts.updated_at,
        })
    sups = (await ctx.db.execute(select(User).where(User.role == UserRole.superviseur))).scalars().all()
    for sup in sups:
        for raw in sup.classes or []:
            try:
                tid = uuid.UUID(str(raw))
            except ValueError:
                ctx.signaler("affectations", sup.id, "affectation_invalide", f"identifiant de tuteur illisible : {raw!r}")
                continue
            if perim is not None and tid not in perim:
                continue
            if ctx.f.superviseur_id and sup.id != ctx.f.superviseur_id:
                continue
            rows.append({
                "affectation_id": cle_derivee("supervision", sup.id, tid),
                "type_affectation": "supervision", "acteur_id": sup.id, "campagne_id": None,
                "tuteur_id": tid, "superviseur_id": sup.id,
                "date_debut": None, "date_fin": None, "datee": 0,
                "created_at": None, "updated_at": sup.updated_at,
            })
    rows.sort(key=lambda r: (r["type_affectation"], str(r["affectation_id"])))
    return rows


async def _eleves_query(ctx: Contexte):
    q = select(Eleve).outerjoin(School, School.id == Eleve.school_id)
    f = ctx.f
    if f.campagne_id:
        q = q.where(Eleve.session_id == f.campagne_id)
    if f.ecole_id:
        q = q.where(Eleve.school_id == f.ecole_id)
    if f.ief:
        q = q.where(func.lower(School.region) == f.ief.strip().lower())
    if f.commune:
        q = q.where(func.lower(School.city) == f.commune.strip().lower())
    if f.tuteur_id:
        tut = await ctx.db.get(User, f.tuteur_id)
        classes = [_norm(c) for c in (tut.classes or [])] if tut else []
        q = q.where(Eleve.school_id == (tut.school_id if tut else None),
                    func.lower(func.regexp_replace(Eleve.classe, r"\s+", " ", "g")).in_(classes or [""]))
    return q.order_by(Eleve.id)


async def _eleves(ctx: Contexte) -> list[Eleve]:
    async def calcul():
        return list((await ctx.db.execute(await _eleves_query(ctx))).scalars())
    return await ctx.memo("eleves", calcul)


async def b_eleves(ctx: Contexte) -> list[dict]:
    return [{
        "eleve_id": ctx.eleve(e.id), "genre": e.genre, "statut": e.statut,
        "statut_selection": e.statut_selection, "groupe_pedagogique_lecture": e.groupe_lecture,
        "groupe_pedagogique_maths": e.groupe_maths, "archive_le": e.archived_at,
        "created_at": e.created_at, "updated_at": e.updated_at,
        "nom": e.nom, "prenom": e.prenom, "code_eleve_source": e.code_eleve, "date_naissance": e.date_naissance,
    } for e in await _eleves(ctx)]


async def b_inscriptions(ctx: Contexte) -> list[dict]:
    """Inscription courante de chaque élève. Limite : l'historique des
    changements de classe n'est pas encore conservé ; les remplacements le
    sont (mouvements_eleves)."""
    idx = await _classes_index(ctx)
    rows = []
    for e in await _eleves(ctx):
        classe_id = idx.get((e.school_id, _norm(e.classe)))
        iid = cle_derivee("inscription", e.id, e.session_id, e.school_id, _norm(e.classe))
        if classe_id is None:
            ctx.signaler("inscriptions", iid, "classe_inconnue",
                         "classe saisie absente du référentiel des classes de l'école", "information")
        if e.school_id is None:
            ctx.signaler("inscriptions", iid, "ecole_absente", "élève sans école de rattachement")
        rows.append({
            "inscription_id": iid, "eleve_id": ctx.eleve(e.id), "campagne_id": e.session_id,
            "ecole_id": e.school_id, "classe_id": classe_id, "classe_libelle": e.classe,
            "statut_selection": e.statut_selection,
            "statut_inscription": "sortie" if e.archived_at else ("active" if e.statut == "actif" else e.statut),
            "date_debut": jour_dakar(e.created_at), "date_fin": jour_dakar(e.archived_at),
            "created_at": e.created_at, "updated_at": e.updated_at,
        })
    return rows


async def b_planning(ctx: Contexte) -> list[dict]:
    q = select(PlanningSegment)
    if ctx.f.campagne_id:
        q = q.where(PlanningSegment.session_id == ctx.f.campagne_id)
    if ctx.f.ecole_id:
        q = q.where(PlanningSegment.school_id == ctx.f.ecole_id)
    return [{
        "planning_id": p.id, "campagne_id": p.session_id, "tuteur_id": p.teacher_id, "ecole_id": p.school_id,
        "semaine_programme": p.semaine, "jour_programme": p.jour,
        "heure_debut": p.heure_debut.strftime("%H:%M"), "heure_fin": p.heure_fin.strftime("%H:%M"),
        "classe_libelle": p.classe, "matiere": p.matiere, "created_at": p.created_at, "updated_at": p.updated_at,
    } for p in (await ctx.db.execute(q.order_by(PlanningSegment.session_id, PlanningSegment.semaine,
                                               PlanningSegment.jour, PlanningSegment.heure_debut,
                                               PlanningSegment.id))).scalars()]


# ── Rapports journaliers et tables liées ─────────────────────────────────────

def _occurrences(col, motif: str):
    """Nombre d'occurrences d'un motif dans un texte, calculé en SQL : on
    compte les photos sans transférer leur base64 (plusieurs Mo par rapport)."""
    return (func.length(func.coalesce(col, "")) - func.length(func.replace(func.coalesce(col, ""), motif, ""))) \
        / len(motif)


RJ_COLS = [
    RapportJournalier.id, RapportJournalier.teacher_id, RapportJournalier.session_id,
    RapportJournalier.ecole_id, RapportJournalier.superviseur_id, RapportJournalier.date_rapport,
    RapportJournalier.semaine, RapportJournalier.jour_cours, RapportJournalier.ief, RapportJournalier.commune,
    RapportJournalier.ecole, RapportJournalier.superviseur, RapportJournalier.nom_tuteur,
    RapportJournalier.nb_absences, RapportJournalier.absents, RapportJournalier.absents_ids,
    RapportJournalier.effectif_attendu, RapportJournalier.difficultes, RapportJournalier.autres_difficultes,
    RapportJournalier.description_difficultes, RapportJournalier.directeur_venu, RapportJournalier.besoin_appui,
    RapportJournalier.domaines_appui, RapportJournalier.has_observations, RapportJournalier.commentaires,
    RapportJournalier.soumis_en_offline, RapportJournalier.reponses_questions, RapportJournalier.client_op_id,
    RapportJournalier.submitted_at, RapportJournalier.created_at, RapportJournalier.updated_at,
    RapportJournalier.statut, RapportJournalier.version, RapportJournalier.statut_motif,
    RapportJournalier.statut_par_id, RapportJournalier.statut_at,
    (_occurrences(RapportJournalier.photos_classe_url, "data:image")
     + _occurrences(RapportJournalier.photos_classe_url, "http")).label("nb_photos_liste"),
    RapportJournalier.photo_classe_url.is_not(None).label("photo_unique"),
]


async def _rapports(ctx: Contexte) -> list[Any]:
    async def calcul():
        f = ctx.f
        q = select(*RJ_COLS, User.role.label("role_auteur"), User.school_id.label("ecole_auteur")) \
            .join(User, User.id == RapportJournalier.teacher_id)
        if f.campagne_id:
            q = q.where(RapportJournalier.session_id == f.campagne_id)
        if f.date_debut:
            q = q.where(RapportJournalier.date_rapport >= f.date_debut)
        if f.date_fin:
            q = q.where(RapportJournalier.date_rapport <= f.date_fin)
        if f.recu_depuis:
            q = q.where(RapportJournalier.created_at >= f.recu_depuis)
        if f.recu_jusqua:
            q = q.where(RapportJournalier.created_at <= f.recu_jusqua)
        if f.ief:
            q = q.where(func.lower(RapportJournalier.ief) == f.ief.strip().lower())
        if f.commune:
            q = q.where(func.lower(RapportJournalier.commune) == f.commune.strip().lower())
        if f.ecole_id:
            q = q.where(RapportJournalier.ecole_id == f.ecole_id)
        if f.tuteur_id:
            q = q.where(RapportJournalier.teacher_id == f.tuteur_id)
        if f.superviseur_id:
            q = q.where(RapportJournalier.superviseur_id == f.superviseur_id)
        if f.statut == "valides":
            q = q.where(RapportJournalier.statut != "annule")
        elif f.statut in ("soumis", "corrige", "annule"):
            q = q.where(RapportJournalier.statut == f.statut)
        q = q.order_by(RapportJournalier.date_rapport, RapportJournalier.created_at, RapportJournalier.id)
        return list((await ctx.db.execute(q)).all())
    return await ctx.memo("rapports", calcul)


async def b_rapports(ctx: Contexte) -> list[dict]:
    rows = []
    for r in await _rapports(ctx):
        nb_photos = int(r.nb_photos_liste or 0) or (1 if r.photo_unique else 0)
        qualite = "valide"
        if r.ecole_id is None:
            ctx.signaler("rapports_journaliers", r.id, "ecole_non_rattachee",
                         "rapport sans école de rattachement à la réception (antérieur au lot L4 ?)", "information")
        if r.superviseur_id is None and r.role_auteur == UserRole.enseignant:
            ctx.signaler("rapports_journaliers", r.id, "superviseur_non_rattache",
                         "aucun superviseur unique connu à la réception", "information")
        if r.absents_ids is not None and len(r.absents_ids) != r.nb_absences:
            qualite = "a_verifier"
            ctx.signaler("rapports_journaliers", r.id, "absences_incoherentes",
                         f"{r.nb_absences} absence(s) déclarée(s), {len(r.absents_ids)} élève(s) identifié(s)")
        if r.effectif_attendu is not None and r.nb_absences > r.effectif_attendu:
            qualite = "a_verifier"
            ctx.signaler("rapports_journaliers", r.id, "absences_superieures_effectif",
                         f"{r.nb_absences} absences pour {r.effectif_attendu} inscrits attendus")
        rows.append({
            "rapport_id": r.id,
            "type_rapport": "superviseur" if r.role_auteur == UserRole.superviseur else "tuteur",
            "campagne_id": r.session_id, "ecole_id": r.ecole_id, "classe_id": None,
            "auteur_id": r.teacher_id, "superviseur_id": r.superviseur_id,
            "date_activite": r.date_rapport, "semaine_programme": r.semaine, "jour_programme": r.jour_cours,
            "nb_absences_declarees": r.nb_absences, "effectif_attendu": r.effectif_attendu,
            "directeur_present_declare": r.directeur_venu, "besoin_appui_declare": r.besoin_appui,
            "observations_declarees": r.has_observations, "soumis_hors_ligne": r.soumis_en_offline,
            "nb_photos": nb_photos,
            "ief_libelle": r.ief, "commune_libelle": r.commune, "ecole_libelle": r.ecole,
            "submitted_at": r.submitted_at, "received_at": r.created_at, "updated_at": r.updated_at,
            "client_operation_id": r.client_op_id, "source_systeme": "application",
            "statut_enregistrement": r.statut, "version": r.version, "motif_correction": r.statut_motif,
            "corrige_par": r.statut_par_id, "statut_at": r.statut_at, "statut_qualite": qualite,
            "nom_tuteur": r.nom_tuteur, "superviseur_libelle": r.superviseur,
        })
    return rows


async def _eleves_par_nom(ctx: Contexte) -> dict[tuple, list[uuid.UUID]]:
    """Index (école, nom normalisé) → élèves, pour rapprocher les absences des
    rapports antérieurs au lot L4 (saisies par nom uniquement)."""
    async def calcul():
        idx: dict[tuple, list[uuid.UUID]] = {}
        for e in (await ctx.db.execute(select(Eleve.id, Eleve.school_id, Eleve.nom, Eleve.prenom))).all():
            for libelle in {_norm(f"{e.prenom} {e.nom}" if e.prenom else e.nom), _norm(f"{e.nom} {e.prenom or ''}")}:
                idx.setdefault((e.school_id, libelle), []).append(e.id)
        return idx
    return await ctx.memo("eleves_par_nom", calcul)


async def b_absences(ctx: Contexte) -> list[dict]:
    """Une absence d'élève déclarée dans un rapport (presences_eleves restreinte
    aux absences : les présents ne sont pas saisis individuellement)."""
    par_nom = await _eleves_par_nom(ctx)
    rows = []
    for r in await _rapports(ctx):
        if r.absents_ids:
            for eid in r.absents_ids:
                rows.append(_absence(ctx, r, eid, "identifiant", None))
            continue
        noms = [n.strip() for n in (r.absents or "").split(",") if n.strip()]
        for nom in noms:
            candidats = par_nom.get((r.ecole_id or r.ecole_auteur, _norm(nom)), [])
            if len(candidats) == 1:
                rows.append(_absence(ctx, r, candidats[0], "nom_rapproche", nom))
            else:
                row = _absence(ctx, r, None, "non_rapproche", nom)
                ctx.signaler("absences_eleves", row["absence_id"], "absent_non_rapproche",
                             "homonymes" if candidats else "nom introuvable dans l'école du rapport")
                rows.append(row)
    return rows


def _absence(ctx: Contexte, r: Any, eid: Any, methode: str, nom: Optional[str]) -> dict:
    return {
        "absence_id": cle_derivee("absence", r.id, eid or nom),
        "rapport_id": r.id, "date_activite": r.date_rapport, "campagne_id": r.session_id,
        "ecole_id": r.ecole_id, "auteur_id": r.teacher_id, "eleve_id": ctx.eleve(eid),
        "statut_presence": "absent", "methode_rattachement": methode,
        "statut_enregistrement": r.statut, "nom_saisi": nom,
    }


async def _catalogue_difficultes(ctx: Contexte) -> dict[str, uuid.UUID]:
    async def calcul():
        return {_norm(d.label): d.id for d in (await ctx.db.execute(select(RapportDifficulte))).scalars()}
    return await ctx.memo("catalogue_difficultes", calcul)


async def _resolutions(ctx: Contexte) -> dict[tuple, Any]:
    async def calcul():
        return {(r.rapport_id, _norm(r.difficulte_label)): r
                for r in (await ctx.db.execute(select(RapportDifficulteResolution))).scalars()}
    return await ctx.memo("resolutions", calcul)


async def b_difficultes(ctx: Contexte) -> list[dict]:
    """Une difficulté choisie dans un rapport (D5.2) : « Aucune » est une
    modalité exportée (code AUCUNE), distincte d'une question non renseignée."""
    cat = await _catalogue_difficultes(ctx)
    res = await _resolutions(ctx)
    rows = []
    for r in await _rapports(ctx):
        choix = _liste_json(r.difficultes)
        if choix is None:
            ctx.signaler("difficultes_signalees", r.id, "difficultes_illisibles", "liste de difficultés illisible")
            continue
        libelles = [str(c).strip() for c in choix if str(c).strip()]
        if any(_norm(l) == "aucune" for l in libelles) and len(libelles) > 1:
            ctx.signaler("difficultes_signalees", r.id, "aucune_non_exclusive",
                         "« Aucune » choisie avec d'autres difficultés")
        for lib in libelles:
            if _norm(lib) == "aucune":
                code = AUCUNE
            elif _norm(lib) in ("autre", "autres"):
                code = AUTRE
            else:
                code = cat.get(_norm(lib))
                if code is None:
                    ctx.signaler("difficultes_signalees", r.id, "difficulte_hors_catalogue",
                                 f"libellé absent du catalogue : {lib}", "information")
            reso = res.get((r.id, _norm(lib)))
            rows.append({
                "signalement_id": cle_derivee("difficulte", r.id, _norm(lib)),
                "rapport_id": r.id, "date_activite": r.date_rapport, "campagne_id": r.session_id,
                "difficulte_code": code, "difficulte_libelle": lib,
                "statut_traitement": None if code == AUCUNE else ("resolue" if reso and reso.resolue else "ouverte"),
                "resolu_le": reso.resolue_le if reso and reso.resolue else None,
                "statut_enregistrement": r.statut,
            })
    return rows


async def b_besoins(ctx: Contexte) -> list[dict]:
    rows = []
    for r in await _rapports(ctx):
        if not r.besoin_appui:
            continue
        domaines = _liste_json(r.domaines_appui)
        if domaines is None:
            domaines = [d.strip() for d in re.split(r"[,;\n]", r.domaines_appui or "") if d.strip()]
        for dom in domaines:
            rows.append({
                "besoin_id": cle_derivee("besoin", r.id, _norm(str(dom))),
                "rapport_id": r.id, "date_activite": r.date_rapport, "campagne_id": r.session_id,
                "domaine_appui_code": None, "domaine_appui_libelle": str(dom),
                "statut_traitement": "non_suivi", "statut_enregistrement": r.statut,
            })
    return rows


async def b_commentaires(ctx: Contexte) -> list[dict]:
    rows = []
    for r in await _rapports(ctx):
        for categorie, texte in (("commentaire", r.commentaires),
                                 ("description_difficultes", r.description_difficultes),
                                 ("autres_difficultes", r.autres_difficultes)):
            if texte and texte.strip():
                rows.append({
                    "commentaire_id": cle_derivee("commentaire", r.id, categorie),
                    "rapport_id": r.id, "auteur_id": r.teacher_id, "date_activite": r.date_rapport,
                    "campagne_id": r.session_id, "categorie": categorie, "longueur": len(texte),
                    "statut_enregistrement": r.statut, "texte": texte,
                })
    return rows


async def _versions_questions(ctx: Contexte) -> dict[str, list[Any]]:
    async def calcul():
        idx: dict[str, list[Any]] = {}
        q = select(RapportQuestionVersion).order_by(RapportQuestionVersion.question_id, RapportQuestionVersion.version)
        for v in (await ctx.db.execute(q)).scalars():
            idx.setdefault(str(v.question_id), []).append(v)
        return idx
    return await ctx.memo("versions_questions", calcul)


def _version_a(versions: list[Any], instant: datetime) -> Optional[Any]:
    choisie = None
    for v in versions:
        if v.valid_from <= instant:
            choisie = v
    return choisie or (versions[0] if versions else None)


async def b_reponses(ctx: Contexte) -> list[dict]:
    """Une réponse à une question dynamique (D5.2), interprétée avec la version
    de la question valide à la réception du rapport (REC13). Choix multiples :
    une ligne par modalité."""
    versions = await _versions_questions(ctx)
    rows = []
    for r in await _rapports(ctx):
        if not r.reponses_questions:
            continue
        try:
            reps = json.loads(r.reponses_questions)
        except (ValueError, TypeError):
            ctx.signaler("reponses_questions", r.id, "reponses_illisibles", "objet JSON illisible")
            continue
        for qid, valeur in (reps or {}).items():
            v = _version_a(versions.get(str(qid), []), r.created_at)
            if v is None:
                ctx.signaler("reponses_questions", r.id, "question_inconnue", f"question {qid} sans définition connue")
            valeurs = valeur if isinstance(valeur, list) else [valeur]
            for rang, val in enumerate(valeurs, start=1):
                if v is not None and v.type == "selection_eleve":
                    val = ctx.eleve(val)
                rows.append({
                    "reponse_id": cle_derivee("reponse", r.id, qid, rang),
                    "rapport_id": r.id, "question_id": qid,
                    "question_version": v.version if v else None, "type_question": v.type if v else None,
                    "rang": rang if isinstance(valeur, list) else None,
                    "valeur": val if not isinstance(val, (dict, list)) else json.dumps(val, ensure_ascii=False),
                    "date_activite": r.date_rapport, "statut_enregistrement": r.statut,
                })
    return rows


# ── Séances, pointages, évaluations, mouvements ──────────────────────────────

async def b_seances(ctx: Contexte) -> list[dict]:
    f = ctx.f
    q = select(Seance)
    if f.campagne_id:
        q = q.where(Seance.session_id == f.campagne_id)
    if f.recu_depuis:
        q = q.where(Seance.created_at >= f.recu_depuis)
    if f.recu_jusqua:
        q = q.where(Seance.created_at <= f.recu_jusqua)
    perim = await tuteurs_perimetre(ctx)
    if perim is not None:
        q = q.where(Seance.teacher_id.in_(perim or [uuid.uuid4()]))
    rows = []
    for s in (await ctx.db.execute(q.order_by(Seance.date_seance, Seance.id))).scalars():
        jour = jour_dakar(s.date_seance)
        if (f.date_debut and jour < f.date_debut) or (f.date_fin and jour > f.date_fin):
            continue
        statut = s.status.value if hasattr(s.status, "value") else s.status
        # Durée OBSERVÉE : séance terminée par le tuteur avec horodatages ; une
        # clôture automatique ou une récupération n'a pas de durée (APP-21).
        observee = s.duree_minutes if (statut == "terminee" and not s.auto_closed
                                       and s.cloture_mode not in ("automatique", "recuperation")) else None
        if statut == "terminee" and observee is None and s.cloture_mode not in ("automatique", "recuperation"):
            ctx.signaler("seances", s.id, "duree_absente", "séance terminée sans durée observée")
        rows.append({
            "seance_id": s.id, "campagne_id": s.session_id, "tuteur_id": s.teacher_id,
            "planning_id": s.planning_segment_id, "classe_libelle": s.classe, "matiere": s.matiere,
            "date_activite": jour, "statut": statut, "debut_at": s.started_at, "fin_at": s.finished_at,
            "duree_observee_minutes": observee, "duree_estimee_minutes": None,
            "pauses_minutes": s.total_paused_minutes, "cloture_mode": s.cloture_mode,
            "cloture_automatique": s.auto_closed, "nb_presents_declare": s.nb_eleves_presents,
            "nb_total_declare": s.nb_eleves_total, "client_operation_id": s.client_seance_id,
            "created_at": s.created_at, "updated_at": s.updated_at,
        })
    return rows


async def b_pointages(ctx: Contexte) -> list[dict]:
    f = ctx.f
    q = select(SupervisorPresenceCheck)
    if f.campagne_id:
        q = q.where(SupervisorPresenceCheck.session_id == f.campagne_id)
    if f.date_debut:
        q = q.where(SupervisorPresenceCheck.date_jour >= f.date_debut)
    if f.date_fin:
        q = q.where(SupervisorPresenceCheck.date_jour <= f.date_fin)
    if f.recu_depuis:
        q = q.where(SupervisorPresenceCheck.created_at >= f.recu_depuis)
    if f.recu_jusqua:
        q = q.where(SupervisorPresenceCheck.created_at <= f.recu_jusqua)
    if f.superviseur_id:
        q = q.where(SupervisorPresenceCheck.superviseur_id == f.superviseur_id)
    perim = await tuteurs_perimetre(ctx) if (f.ief or f.commune or f.ecole_id or f.tuteur_id) else None
    if perim is not None:
        q = q.where(SupervisorPresenceCheck.teacher_id.in_(perim or [uuid.uuid4()]))
    return [{
        "pointage_id": p.id, "campagne_id": p.session_id, "tuteur_id": p.teacher_id,
        "superviseur_id": p.superviseur_id, "date_activite": p.date_jour, "present": p.present,
        "motif": p.motif, "semaine_programme": p.semaine, "jour_programme": p.jour_cours,
        "created_at": p.created_at, "updated_at": p.updated_at,
    } for p in (await ctx.db.execute(q.order_by(SupervisorPresenceCheck.date_jour, SupervisorPresenceCheck.id))).scalars()]


async def _eleves_autorises(ctx: Contexte) -> Optional[set[uuid.UUID]]:
    """Élèves couverts par les filtres école / IEF / commune / tuteur (la
    campagne d'une évaluation se filtre sur l'évaluation elle-même)."""
    f = ctx.f
    if not (f.ecole_id or f.ief or f.commune or f.tuteur_id):
        return None
    sans_campagne = Contexte(ctx.db, Filtres(ecole_id=f.ecole_id, ief=f.ief, commune=f.commune, tuteur_id=f.tuteur_id))
    return set((await ctx.db.execute((await _eleves_query(sans_campagne)).with_only_columns(Eleve.id))).scalars())


async def b_evaluations(ctx: Contexte) -> list[dict]:
    """Vue harmonisée des DEUX sources d'évaluation (APP-36, D3.3) :
    - « competence » : évaluations par compétence (evaluations_eleves) ;
    - « sujet » : résultats datés des tirages sur sujet (evaluation_resultats),
      un résultat par tirage et par date ;
    - « sujet_historique » : états remplacés par une réévaluation le même jour
      (non valides, conservés pour l'historique, REC12) ;
    - « sujet_etat » : tirage évalué sans résultat daté (données anciennes).
    La source, l'instrument et la date sont toujours conservés."""
    f = ctx.f
    autorises = await _eleves_autorises(ctx)
    rows: list[dict] = []

    def garder(eleve_id: uuid.UUID, d: Optional[date], sup: Optional[uuid.UUID]) -> bool:
        if autorises is not None and eleve_id not in autorises:
            return False
        if d is not None and ((f.date_debut and d < f.date_debut) or (f.date_fin and d > f.date_fin)):
            return False
        if f.superviseur_id and sup != f.superviseur_id:
            return False
        return True

    q1 = select(EvaluationEleve)
    if f.campagne_id:
        q1 = q1.where(EvaluationEleve.session_id == f.campagne_id)
    for e in (await ctx.db.execute(q1.order_by(EvaluationEleve.date_eval, EvaluationEleve.id))).scalars():
        if not garder(e.eleve_id, e.date_eval, e.superviseur_id):
            continue
        rows.append(_eval_row(ctx, e.id, "competence", e.competence, e.competence, None, None, e.session_id,
                              e.eleve_id, e.superviseur_id, e.date_eval, e.resultat, "passe", 1, None,
                              None, e.created_at, e.updated_at))

    q2 = (select(EvaluationResultat, EvaluationTirage, EvaluationSujet)
          .join(EvaluationTirage, EvaluationTirage.id == EvaluationResultat.tirage_id)
          .join(EvaluationSujet, EvaluationSujet.id == EvaluationTirage.sujet_id))
    if f.campagne_id:
        q2 = q2.where(EvaluationSujet.session_id == f.campagne_id)
    avec_resultat: set[uuid.UUID] = set()
    for res, tir, suj in (await ctx.db.execute(q2.order_by(EvaluationResultat.date_eval, EvaluationResultat.id))).all():
        avec_resultat.add(tir.id)
        if not garder(tir.eleve_id, res.date_eval, res.superviseur_id):
            continue
        rows.append(_eval_row(ctx, res.id, "sujet", str(suj.id), str(suj.id), suj.langue, tir.id, suj.session_id,
                              tir.eleve_id, res.superviseur_id, res.date_eval, res.resultat,
                              _passation(tir.present), 1, res.id if res.audio_filename else None,
                              None, res.created_at, res.updated_at))

    q3 = (select(EvaluationResultatHistorique, EvaluationTirage, EvaluationSujet, EvaluationResultat.date_eval)
          .join(EvaluationTirage, EvaluationTirage.id == EvaluationResultatHistorique.tirage_id)
          .join(EvaluationSujet, EvaluationSujet.id == EvaluationTirage.sujet_id)
          .join(EvaluationResultat, EvaluationResultat.id == EvaluationResultatHistorique.resultat_id))
    if f.campagne_id:
        q3 = q3.where(EvaluationSujet.session_id == f.campagne_id)
    for h, tir, suj, d in (await ctx.db.execute(q3.order_by(EvaluationResultatHistorique.observe_at,
                                                            EvaluationResultatHistorique.id))).all():
        if not garder(tir.eleve_id, d, h.superviseur_id):
            continue
        rows.append(_eval_row(ctx, h.id, "sujet_historique", str(suj.id), str(suj.id), suj.langue, tir.id,
                              suj.session_id, tir.eleve_id, h.superviseur_id, d, h.resultat,
                              _passation(tir.present), 0, h.id if h.audio_filename else None,
                              h.remplace_at, h.observe_at, h.remplace_at))

    q4 = (select(EvaluationTirage, EvaluationSujet)
          .join(EvaluationSujet, EvaluationSujet.id == EvaluationTirage.sujet_id)
          .where(EvaluationTirage.resultat.is_not(None)))
    if f.campagne_id:
        q4 = q4.where(EvaluationSujet.session_id == f.campagne_id)
    for tir, suj in (await ctx.db.execute(q4.order_by(EvaluationTirage.id))).all():
        if tir.id in avec_resultat or not garder(tir.eleve_id, tir.date_eval, tir.superviseur_id):
            continue
        if tir.date_eval is None:
            ctx.signaler("evaluations", tir.id, "evaluation_sans_date", "tirage évalué sans date d'évaluation")
        rows.append(_eval_row(ctx, tir.id, "sujet_etat", str(suj.id), str(suj.id), suj.langue, tir.id,
                              suj.session_id, tir.eleve_id, tir.superviseur_id, tir.date_eval, tir.resultat,
                              _passation(tir.present), 1, tir.id if tir.audio_filename else None,
                              None, tir.created_at, tir.updated_at))
    return rows


def _passation(present: Optional[bool]) -> Optional[str]:
    return None if present is None else ("passe" if present else "absent")


def _eval_row(ctx, eid, source, instrument, item, langue, tirage_id, campagne, eleve_id, sup, d, resultat,
              passation, valide, media, remplace_le, created, updated) -> dict:
    harmonise = RESULTAT_HARMONISE.get(resultat or "")
    if resultat and harmonise is None:
        ctx.signaler("evaluations", eid, "resultat_hors_code", f"résultat non harmonisable : {resultat}", "information")
    return {
        "evaluation_id": eid, "source": source, "instrument_id": instrument, "item_evaluation_id": item,
        "langue_code": langue, "tirage_id": tirage_id, "campagne_id": campagne, "eleve_id": ctx.eleve(eleve_id),
        "superviseur_id": sup, "date_evaluation": d, "occasion_evaluation": d.isoformat() if d else None,
        "resultat_code": resultat, "resultat_harmonise": harmonise, "statut_passation": passation,
        "valide": valide, "media_id": media, "audio_disponible": media is not None,
        "remplace_le": remplace_le, "created_at": created, "updated_at": updated,
    }


async def b_mouvements(ctx: Contexte) -> list[dict]:
    f = ctx.f
    q = select(EleveRemplacement)
    if f.ecole_id:
        q = q.where(EleveRemplacement.school_id == f.ecole_id)
    if f.tuteur_id:
        q = q.where(EleveRemplacement.teacher_id == f.tuteur_id)
    if f.date_debut:
        q = q.where(EleveRemplacement.date_remplacement >= f.date_debut)
    if f.date_fin:
        q = q.where(EleveRemplacement.date_remplacement <= f.date_fin)
    if f.campagne_id:
        q = q.join(Eleve, Eleve.id == EleveRemplacement.nouveau_eleve_id).where(Eleve.session_id == f.campagne_id)
    return [{
        "mouvement_id": m.id, "type_mouvement": "remplacement", "ancien_eleve_id": ctx.eleve(m.ancien_eleve_id),
        "nouveau_eleve_id": ctx.eleve(m.nouveau_eleve_id), "tuteur_id": m.teacher_id, "ecole_id": m.school_id,
        "classe_libelle": m.classe, "date_effet": m.date_remplacement,
        "created_at": m.created_at, "updated_at": m.updated_at, "motif": m.motif,
    } for m in (await ctx.db.execute(q.order_by(EleveRemplacement.date_remplacement, EleveRemplacement.id))).scalars()]


async def b_dictionnaire_codes(ctx: Contexte) -> list[dict]:
    rows = [{"catalogue": "difficulte", "code": AUCUNE, "libelle": "Aucune difficulté (exclusive)",
             "actif": 1, "version": None, "valide_depuis": None},
            {"catalogue": "difficulte", "code": AUTRE, "libelle": "Autre difficulté (texte libre)",
             "actif": 1, "version": None, "valide_depuis": None}]
    for d in (await ctx.db.execute(select(RapportDifficulte).order_by(RapportDifficulte.ordre, RapportDifficulte.id))).scalars():
        rows.append({"catalogue": "difficulte", "code": str(d.id), "libelle": d.label, "actif": d.active,
                     "version": None, "valide_depuis": d.created_at})
    for c in (await ctx.db.execute(select(EvaluationCompetence).order_by(EvaluationCompetence.ordre))).scalars():
        rows.append({"catalogue": "competence", "code": c.code, "libelle": c.label, "actif": c.active,
                     "version": None, "valide_depuis": c.created_at})
    for s in (await ctx.db.execute(select(EvaluationSujet).order_by(EvaluationSujet.created_at))).scalars():
        rows.append({"catalogue": "instrument_sujet", "code": str(s.id), "libelle": s.titre, "actif": 1,
                     "version": None, "valide_depuis": s.created_at})
    actives = {str(q.id): q for q in (await ctx.db.execute(select(RapportQuestion))).scalars()}
    for qid, versions in (await _versions_questions(ctx)).items():
        for v in versions:
            q = actives.get(qid)
            rows.append({"catalogue": f"question:{v.type}", "code": f"{qid}@{v.version}", "libelle": v.label,
                         "actif": bool(q and q.active and q.version == v.version),
                         "version": v.version, "valide_depuis": v.valid_from})
    for code, lib in (("acquis", "Acquis"), ("en_cours", "En cours"), ("a_aider", "À aider")):
        rows.append({"catalogue": "resultat_harmonise", "code": code, "libelle": lib, "actif": 1,
                     "version": None, "valide_depuis": None})
    return rows


# ── Catalogue des tables (ordre du pack) ─────────────────────────────────────

C = Col
TS = [C("created_at", "horodatage", "Création serveur", "technique"),
      C("updated_at", "horodatage", "Dernière modification serveur (reprise incrémentale)", "technique")]
STATUT_ENR = C("statut_enregistrement", "code", "Statut du rapport source", "saisi", "soumis|corrige|annule")

TABLES: list[Table] = [
    Table("campagnes", "Une campagne du programme", "campagne_id", [
        C("campagne_id", "identifiant", "Campagne (session de programme)", "technique"),
        C("nom", "texte", "Libellé de la campagne"),
        C("type_campagne", "code", "Type validé par ARED (non collecté : vide)", "reference"),
        C("date_debut", "date", "Début de campagne"), C("date_fin", "date", "Fin de campagne"),
        C("statut", "code", "Statut de la campagne", "saisi", "active|inactive"),
        C("archive_le", "horodatage", "Archivage (vide si active)", "technique"), *TS,
    ], b_campagnes),
    Table("ecoles", "Une école", "ecole_id", [
        C("ecole_id", "identifiant", "École canonique", "technique"),
        C("code_ecole", "code", "Code métier (texte, zéros conservés)"),
        C("nom", "texte", "Nom de l'école"), C("ief_libelle", "texte", "IEF / région saisie"),
        C("commune_libelle", "texte", "Commune / ville saisie"), C("langue", "code", "Langue d'enseignement"),
        C("archive_le", "horodatage", "Archivage", "technique"), *TS,
    ], b_ecoles),
    Table("classes", "Une classe d'une école", "classe_id", [
        C("classe_id", "identifiant", "Classe", "technique"), C("ecole_id", "identifiant", "École", "reference"),
        C("campagne_id", "identifiant", "Campagne (non rattachée aujourd'hui : vide)", "reference"),
        C("nom", "texte", "Nom de la classe"), C("niveau", "code", "Niveau"),
        C("effectif_declare", "entier", "Effectif saisi dans le référentiel (vide = non renseigné)"),
        C("archive_le", "horodatage", "Archivage", "technique"), *TS,
    ], b_classes),
    Table("acteurs", "Un tuteur ou un superviseur", "acteur_id", [
        C("acteur_id", "identifiant", "Acteur (compte)", "technique"),
        C("role_acteur", "code", "Rôle", "saisi", "enseignant|superviseur"),
        C("statut", "code", "Statut du compte", "saisi", "actif|inactif|en_attente"),
        C("ecole_id", "identifiant", "École de rattachement actuelle", "reference"),
        C("archive_le", "horodatage", "Archivage", "technique"), *TS,
        C("nom", "texte", "Nom", nominatif=True),
        C("groupe_recherche", "code", "Assignation du protocole (droits spécifiques)", nominatif=True),
    ], b_acteurs),
    Table("affectations", "Une responsabilité d'un acteur", "affectation_id", [
        C("affectation_id", "identifiant", "Affectation (clé dérivée stable)", "derive"),
        C("type_affectation", "code", "Type", "derive", "tuteur_campagne|supervision"),
        C("acteur_id", "identifiant", "Acteur affecté", "reference"),
        C("campagne_id", "identifiant", "Campagne", "reference"),
        C("tuteur_id", "identifiant", "Tuteur concerné", "reference"),
        C("superviseur_id", "identifiant", "Superviseur (supervision)", "reference"),
        C("date_debut", "date", "Début de validité (vide = non daté)"),
        C("date_fin", "date", "Fin de validité (vide = en cours ou non daté)"),
        C("datee", "booleen", "1 si les dates de validité sont connues", "derive"), *TS,
    ], b_affectations, note="Les affectations de supervision ne sont pas encore datées (décision ARED)."),
    Table("eleves", "Un enfant", "eleve_id", [
        C("eleve_id", "identifiant", "Élève (opaque en mode pseudonymisé, UUID en mode nominatif)", "technique"),
        C("genre", "code", "Genre saisi"), C("statut", "code", "Statut", "saisi", "actif|archive|…"),
        C("statut_selection", "code", "Titulaire ou remplaçant"),
        C("groupe_pedagogique_lecture", "code", "Groupe de lecture"),
        C("groupe_pedagogique_maths", "code", "Groupe de mathématiques"),
        C("archive_le", "horodatage", "Archivage (sortie)", "technique"), *TS,
        C("nom", "texte", "Nom", nominatif=True), C("prenom", "texte", "Prénom", nominatif=True),
        C("code_eleve_source", "code", "Code élève source", nominatif=True),
        C("date_naissance", "date", "Date de naissance", nominatif=True),
    ], b_eleves),
    Table("inscriptions", "L'inscription d'un enfant dans une classe et une campagne", "inscription_id", [
        C("inscription_id", "identifiant", "Inscription (clé dérivée stable)", "derive"),
        C("eleve_id", "identifiant", "Élève", "reference"), C("campagne_id", "identifiant", "Campagne", "reference"),
        C("ecole_id", "identifiant", "École", "reference"),
        C("classe_id", "identifiant", "Classe du référentiel (vide si non rapprochée)", "rapproche"),
        C("classe_libelle", "texte", "Classe saisie"), C("statut_selection", "code", "Titulaire ou remplaçant"),
        C("statut_inscription", "code", "État", "derive", "active|sortie|…"),
        C("date_debut", "date", "Début (création de la fiche)", "derive"),
        C("date_fin", "date", "Fin (archivage)", "derive"), *TS,
    ], b_inscriptions, note="Inscription courante ; l'historique des changements de classe n'est pas conservé."),
    Table("planning", "Un créneau planifié", "planning_id", [
        C("planning_id", "identifiant", "Créneau", "technique"), C("campagne_id", "identifiant", "Campagne", "reference"),
        C("tuteur_id", "identifiant", "Tuteur (vide = créneau d'école)", "reference"),
        C("ecole_id", "identifiant", "École", "reference"),
        C("semaine_programme", "entier", "Semaine (vide = toutes)"), C("jour_programme", "entier", "Jour (0 = lundi)"),
        C("heure_debut", "texte", "Heure de début HH:MM"), C("heure_fin", "texte", "Heure de fin HH:MM"),
        C("classe_libelle", "texte", "Classe"), C("matiere", "texte", "Activité"), *TS,
    ], b_planning),
    Table("rapports_journaliers", "Un rapport d'un auteur pour une date", "rapport_id", [
        C("rapport_id", "identifiant", "Rapport", "technique"),
        C("type_rapport", "code", "Rapport tuteur ou superviseur", "derive", "tuteur|superviseur"),
        C("campagne_id", "identifiant", "Campagne active à la réception", "reference"),
        C("ecole_id", "identifiant", "École de l'auteur à la réception", "reference"),
        C("classe_id", "identifiant", "Classe (un rapport couvre aujourd'hui toutes les classes du tuteur : vide)", "reference"),
        C("auteur_id", "identifiant", "Auteur", "reference"),
        C("superviseur_id", "identifiant", "Superviseur responsable à la réception (vide si inconnu ou ambigu)", "reference"),
        C("date_activite", "date", "Jour de l'activité"),
        C("semaine_programme", "entier", "Semaine déclarée"), C("jour_programme", "entier", "Jour de cours déclaré"),
        C("nb_absences_declarees", "entier", "Absences déclarées"),
        C("effectif_attendu", "entier", "Inscrits éligibles à la réception (vide si inconnu)", "derive"),
        C("directeur_present_declare", "booleen", "Directeur venu (≠ présence du superviseur)"),
        C("besoin_appui_declare", "booleen", "Besoin d'appui exprimé"),
        C("observations_declarees", "booleen", "Observations signalées"),
        C("soumis_hors_ligne", "booleen", "Saisi hors connexion"),
        C("nb_photos", "entier", "Photos disponibles (médias hors pack)", "derive"),
        C("ief_libelle", "texte", "IEF saisie"), C("commune_libelle", "texte", "Commune saisie"),
        C("ecole_libelle", "texte", "École saisie"),
        C("submitted_at", "horodatage", "Saisie sur le téléphone"),
        C("received_at", "horodatage", "Réception serveur", "technique"),
        C("updated_at", "horodatage", "Dernière modification", "technique"),
        C("client_operation_id", "texte", "Clé d'opération (idempotence)", "technique"),
        C("source_systeme", "code", "Source", "technique", "application"),
        STATUT_ENR, C("version", "entier", "Version du rapport", "technique"),
        C("motif_correction", "texte", "Motif de correction ou d'annulation", "corrige"),
        C("corrige_par", "identifiant", "Auteur de la correction", "corrige"),
        C("statut_at", "horodatage", "Date du dernier changement de statut", "corrige"),
        C("statut_qualite", "code", "Contrôles", "derive", "valide|a_verifier"),
        C("nom_tuteur", "texte", "Nom du tuteur saisi", nominatif=True),
        C("superviseur_libelle", "texte", "Superviseur saisi", nominatif=True),
    ], b_rapports),
    Table("absences_eleves", "Une absence d'élève déclarée dans un rapport", "absence_id", [
        C("absence_id", "identifiant", "Absence (clé dérivée)", "derive"),
        C("rapport_id", "identifiant", "Rapport", "reference"), C("date_activite", "date", "Jour"),
        C("campagne_id", "identifiant", "Campagne", "reference"), C("ecole_id", "identifiant", "École", "reference"),
        C("auteur_id", "identifiant", "Auteur du rapport", "reference"),
        C("eleve_id", "identifiant", "Élève (vide si non rapproché)", "rapproche"),
        C("statut_presence", "code", "Statut", "saisi", "absent"),
        C("methode_rattachement", "code", "Rattachement", "derive", "identifiant|nom_rapproche|non_rapproche"),
        STATUT_ENR, C("nom_saisi", "texte", "Nom saisi (rapports sans identifiant)", nominatif=True),
    ], b_absences, maj=None, note="Les présents ne sont pas saisis individuellement : une absence de ligne ne prouve pas la présence."),
    Table("difficultes_signalees", "Un type de difficulté cité dans un rapport", "signalement_id", [
        C("signalement_id", "identifiant", "Signalement (clé dérivée)", "derive"),
        C("rapport_id", "identifiant", "Rapport", "reference"), C("date_activite", "date", "Jour"),
        C("campagne_id", "identifiant", "Campagne", "reference"),
        C("difficulte_code", "code", "Code du catalogue (AUCUNE, AUTRE, ou identifiant)", "rapproche"),
        C("difficulte_libelle", "texte", "Libellé choisi"),
        C("statut_traitement", "code", "Suivi", "saisi", "ouverte|resolue (vide pour AUCUNE)"),
        C("resolu_le", "horodatage", "Date de résolution"), STATUT_ENR,
    ], b_difficultes, maj=None),
    Table("besoins_appui", "Un domaine d'appui demandé", "besoin_id", [
        C("besoin_id", "identifiant", "Besoin (clé dérivée)", "derive"),
        C("rapport_id", "identifiant", "Rapport", "reference"), C("date_activite", "date", "Jour"),
        C("campagne_id", "identifiant", "Campagne", "reference"),
        C("domaine_appui_code", "code", "Code (catalogue non défini : vide)", "reference"),
        C("domaine_appui_libelle", "texte", "Domaine saisi"),
        C("statut_traitement", "code", "Suivi", "derive", "non_suivi"), STATUT_ENR,
    ], b_besoins, maj=None, note="Aucun circuit de traitement des demandes d'appui n'existe encore : statut non_suivi."),
    Table("commentaires", "Un texte libre relié à son rapport", "commentaire_id", [
        C("commentaire_id", "identifiant", "Commentaire (clé dérivée)", "derive"),
        C("rapport_id", "identifiant", "Rapport", "reference"), C("auteur_id", "identifiant", "Auteur", "reference"),
        C("date_activite", "date", "Jour"), C("campagne_id", "identifiant", "Campagne", "reference"),
        C("categorie", "code", "Champ source", "derive", "commentaire|description_difficultes|autres_difficultes"),
        C("longueur", "entier", "Nombre de caractères", "derive"), STATUT_ENR,
        C("texte", "texte", "Texte saisi", nominatif=True),
    ], b_commentaires, maj=None, note="Textes libres réservés au mode nominatif (minimisation)."),
    Table("reponses_questions", "Une réponse à une question dynamique", "reponse_id", [
        C("reponse_id", "identifiant", "Réponse (clé dérivée)", "derive"),
        C("rapport_id", "identifiant", "Rapport", "reference"), C("question_id", "identifiant", "Question", "reference"),
        C("question_version", "entier", "Version de la question à la réception", "rapproche"),
        C("type_question", "code", "Type de la question"),
        C("rang", "entier", "Rang dans un choix multiple (vide sinon)", "derive"),
        C("valeur", "texte", "Valeur saisie"), C("date_activite", "date", "Jour"), STATUT_ENR,
    ], b_reponses, maj=None),
    Table("seances", "Une séance de minuteur", "seance_id", [
        C("seance_id", "identifiant", "Séance", "technique"), C("campagne_id", "identifiant", "Campagne", "reference"),
        C("tuteur_id", "identifiant", "Tuteur", "reference"), C("planning_id", "identifiant", "Créneau", "reference"),
        C("classe_libelle", "texte", "Classe"), C("matiere", "texte", "Activité"),
        C("date_activite", "date", "Jour (Africa/Dakar)", "derive"),
        C("statut", "code", "Statut", "saisi", "en_cours|terminee|annulee|manquee"),
        C("debut_at", "horodatage", "Début mesuré"), C("fin_at", "horodatage", "Fin mesurée"),
        C("duree_observee_minutes", "decimal", "Durée mesurée (séances terminées par le tuteur)", "derive"),
        C("duree_estimee_minutes", "decimal", "Durée estimée (aucune estimation produite : vide)", "derive"),
        C("pauses_minutes", "entier", "Pauses"),
        C("cloture_mode", "code", "Mode de clôture", "saisi", "tuteur|tuteur_tardive|automatique|recuperation"),
        C("cloture_automatique", "booleen", "Clôture automatique (oubli)"),
        C("nb_presents_declare", "entier", "Présents déclarés"), C("nb_total_declare", "entier", "Total déclaré"),
        C("client_operation_id", "texte", "Clé client de la séance", "technique"), *TS,
    ], b_seances),
    Table("pointages_tuteurs", "Un pointage d'un tuteur par un superviseur à une date", "pointage_id", [
        C("pointage_id", "identifiant", "Pointage", "technique"), C("campagne_id", "identifiant", "Campagne", "reference"),
        C("tuteur_id", "identifiant", "Tuteur", "reference"), C("superviseur_id", "identifiant", "Superviseur", "reference"),
        C("date_activite", "date", "Jour"), C("present", "booleen", "Tuteur présent"),
        C("motif", "texte", "Motif d'absence"), C("semaine_programme", "entier", "Semaine"),
        C("jour_programme", "entier", "Jour"), *TS,
    ], b_pointages),
    Table("evaluations", "Une observation élève sur un instrument à une date", "evaluation_id", [
        C("evaluation_id", "identifiant", "Observation", "technique"),
        C("source", "code", "Source", "technique", "competence|sujet|sujet_historique|sujet_etat"),
        C("instrument_id", "code", "Instrument (code compétence ou sujet)", "reference"),
        C("item_evaluation_id", "code", "Item évalué", "reference"), C("langue_code", "code", "Langue"),
        C("tirage_id", "identifiant", "Tirage (sources sujet)", "reference"),
        C("campagne_id", "identifiant", "Campagne", "reference"), C("eleve_id", "identifiant", "Élève", "reference"),
        C("superviseur_id", "identifiant", "Évaluateur", "reference"), C("date_evaluation", "date", "Date"),
        C("occasion_evaluation", "code", "Occasion (date, en attendant les vagues ARED)", "derive"),
        C("resultat_code", "code", "Résultat dans le code de la source"),
        C("resultat_harmonise", "code", "Résultat harmonisé", "derive", "acquis|en_cours|a_aider"),
        C("statut_passation", "code", "Passation", "saisi", "passe|absent"),
        C("valide", "booleen", "0 si l'état a été remplacé par une réévaluation", "derive"),
        C("media_id", "identifiant", "Audio protégé (hors pack)", "reference"),
        C("audio_disponible", "booleen", "Un audio existe", "derive"),
        C("remplace_le", "horodatage", "Remplacement par une réévaluation", "corrige"), *TS,
    ], b_evaluations),
    Table("mouvements_eleves", "Un changement d'inscription (remplacement)", "mouvement_id", [
        C("mouvement_id", "identifiant", "Mouvement", "technique"),
        C("type_mouvement", "code", "Type", "derive", "remplacement"),
        C("ancien_eleve_id", "identifiant", "Élève sortant", "reference"),
        C("nouveau_eleve_id", "identifiant", "Élève entrant", "reference"),
        C("tuteur_id", "identifiant", "Tuteur", "reference"), C("ecole_id", "identifiant", "École", "reference"),
        C("classe_libelle", "texte", "Classe"), C("date_effet", "date", "Date d'effet"), *TS,
        C("motif", "texte", "Motif saisi", nominatif=True),
    ], b_mouvements),
    Table("dictionnaire_codes", "Une modalité d'un catalogue", "catalogue+code", [
        C("catalogue", "code", "Catalogue", "reference"), C("code", "code", "Code stable", "reference"),
        C("libelle", "texte", "Libellé"), C("actif", "booleen", "Modalité active"),
        C("version", "entier", "Version (questions)"), C("valide_depuis", "horodatage", "Début de validité"),
    ], b_dictionnaire_codes, maj=None),
    # Modules non collectés par l'application : fichiers vides avec en-tête.
    Table("presences_eleves", "Un statut de présence d'un inscrit pour un appel", "presence_id", [
        C("presence_id", "identifiant", "Observation de présence"), C("inscription_id", "identifiant", "Inscription"),
        C("appel_id", "identifiant", "Appel"), C("rapport_id", "identifiant", "Rapport"),
        C("statut_presence", "code", "Statut", "saisi", "present|absent|non_renseigne"),
    ], None, "non_collecte", "L'appel individuel (présents) n'est pas saisi : seules les absences le sont (absences_eleves).", None),
    Table("calendrier_attendu", "Une obligation planifiée", "attendu_id", [
        C("attendu_id", "identifiant", "Obligation"), C("campagne_id", "identifiant", "Campagne"),
        C("acteur_id", "identifiant", "Acteur"), C("date_activite", "date", "Jour attendu"),
        C("type_activite", "code", "Type d'activité"),
    ], None, "non_collecte", "Objectifs et exceptions par rôle à valider par ARED (Annexe D).", None),
    Table("visites_supervision", "Une visite pédagogique", "visite_id", [
        C("visite_id", "identifiant", "Visite"), C("classe_id", "identifiant", "Classe"),
        C("tuteur_id", "identifiant", "Tuteur"), C("superviseur_id", "identifiant", "Superviseur"),
        C("date_activite", "date", "Jour"), C("grille_id", "code", "Grille"), C("version_grille", "code", "Version"),
    ], None, "non_collecte", "Module de visite et grille versionnée à définir avec ARED (Annexe D).", None),
    Table("items_supervision", "Une réponse à un item de grille", "visite_id+item_id+version_grille", [
        C("visite_id", "identifiant", "Visite"), C("item_id", "code", "Item"), C("version_grille", "code", "Version"),
        C("reponse_code", "code", "Réponse"), C("applicable", "booleen", "Applicable"), C("renseigne", "booleen", "Renseigné"),
    ], None, "non_collecte", "Voir visites_supervision.", None),
]

TABLES_PAR_NOM = {t.nom: t for t in TABLES}
