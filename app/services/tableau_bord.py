"""Vues de tableau de bord (lot L6, Partie II D8).

Chaque vue renvoie ses lignes détaillées (pour retrouver ce qui explique un
agrégat), l'unité de chaque mesure, les règles appliquées et, si les données
nécessaires ne sont pas configurées ou collectées, une raison de non-calcul
au lieu d'une valeur approchée (D4.4).

Filtres communs : campagne (par défaut la campagne active la plus récente),
période (bornée par la campagne et par aujourd'hui), région, commune, école,
tuteur. Les listes nominatives sont réservées à l'administration (routes
admin) ; l'accès de service n'obtient que des agrégats et des identifiants.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demande_appui import STATUTS_DEMANDE_OUVERTS, DemandeAppui
from app.models.eleve import Eleve
from app.models.presence_eleve import PresenceEleve
from app.models.rapport_journalier import RAPPORT_COMPTE, RapportJournalier
from app.models.school import School
from app.models.school_classe import SchoolClasse
from app.models.session import ProgramSession, TeacherSession
from app.models.supervisor_presence import SupervisorPresenceCheck
from app.models.user import User, UserRole, UserStatus
from app.models.visite import GrilleVisiteItem, Visite
from app.services import campagne_service as camp
from app.services.supervisor_service import get_supervised_teacher_ids


@dataclass
class Filtres:
    session_id: Optional[uuid.UUID] = None
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    region: Optional[str] = None
    commune: Optional[str] = None
    school_id: Optional[uuid.UUID] = None
    teacher_id: Optional[uuid.UUID] = None


def _non_calculable(raison: str, **extra) -> dict:
    return {"statut": "non_calculable", "raison": raison, "lignes": [], "totaux": {}, **extra}


def _periode(s: ProgramSession, f: Filtres) -> tuple[date, date]:
    debut = max(s.date_debut, f.date_debut) if f.date_debut else s.date_debut
    fin = min(s.date_fin, f.date_fin or date.today(), date.today())
    return debut, fin


async def _ecoles(db: AsyncSession, f: Filtres) -> Optional[set[uuid.UUID]]:
    """Écoles retenues par les filtres géographiques (None = pas de filtre)."""
    if not (f.region or f.commune or f.school_id):
        return None
    q = select(School.id)
    if f.school_id:
        q = q.where(School.id == f.school_id)
    if f.region:
        q = q.where(func.lower(School.region) == f.region.strip().lower())
    if f.commune:
        q = q.where(func.lower(School.city) == f.commune.strip().lower())
    return set((await db.execute(q)).scalars().all())


async def _tuteurs(db: AsyncSession, s: ProgramSession, f: Filtres) -> list[tuple[User, date]]:
    """Tuteurs affectés à la campagne, avec leur date d'arrivée (affectation datée)."""
    ecoles = await _ecoles(db, f)
    q = (select(User, TeacherSession.created_at)
         .join(TeacherSession, TeacherSession.teacher_id == User.id)
         .where(TeacherSession.session_id == s.id, User.role == UserRole.enseignant))
    if f.teacher_id:
        q = q.where(User.id == f.teacher_id)
    if ecoles is not None:
        q = q.where(User.school_id.in_(ecoles))
    return [(u, max(s.date_debut, cree.date())) for u, cree in (await db.execute(q.order_by(User.name))).all()]


def _fin_acteur(u: User, fin: date) -> date:
    if u.archived_at is not None:
        return min(fin, u.archived_at.date())
    return fin


async def _noms_ecoles(db: AsyncSession) -> dict[uuid.UUID, School]:
    return {sc.id: sc for sc in (await db.execute(select(School))).scalars()}


# ── Vue d'ensemble ────────────────────────────────────────────────────────────

async def vue_ensemble(db: AsyncSession, f: Filtres) -> dict:
    s = await camp.session_ou_active(db, f.session_id)
    ecoles = await _ecoles(db, f)
    q_ecoles = select(func.count()).select_from(School).where(School.archived_at.is_(None))
    q_classes = select(func.count()).select_from(SchoolClasse).where(SchoolClasse.archived_at.is_(None))
    q_eleves = select(func.count()).select_from(Eleve).where(Eleve.archived_at.is_(None), Eleve.statut == "actif")
    if ecoles is not None:
        q_ecoles = q_ecoles.where(School.id.in_(ecoles))
        q_classes = q_classes.where(SchoolClasse.school_id.in_(ecoles))
        q_eleves = q_eleves.where(Eleve.school_id.in_(ecoles))
    if s is not None:
        q_eleves = q_eleves.where(Eleve.session_id == s.id) if (await db.execute(
            select(Eleve.id).where(Eleve.session_id == s.id).limit(1))).first() else q_eleves
    tuteurs = await _tuteurs(db, s, f) if s else []
    q_r = select(func.count(), func.max(RapportJournalier.created_at)).where(RAPPORT_COMPTE)
    q_v = select(func.count(), func.max(Visite.created_at)).where(Visite.statut == "soumise")
    if s is not None:
        debut, fin = _periode(s, f)
        q_r = q_r.where(RapportJournalier.session_id == s.id, RapportJournalier.date_rapport.between(debut, fin))
        q_v = q_v.where(Visite.session_id == s.id, Visite.date_visite.between(debut, fin))
    if ecoles is not None:
        q_r = q_r.where(RapportJournalier.ecole_id.in_(ecoles))
        q_v = q_v.where(Visite.school_id.in_(ecoles))
    nb_r, dernier_r = (await db.execute(q_r)).one()
    nb_v, derniere_v = (await db.execute(q_v)).one()
    q_p = select(func.count(PresenceEleve.id), func.count(func.distinct(PresenceEleve.eleve_id)))
    if s is not None:
        q_p = q_p.where(PresenceEleve.session_id == s.id)
    nb_p, nb_eleves_appel = (await db.execute(q_p)).one()
    fraicheur = max([d for d in (dernier_r, derniere_v) if d], default=None)
    return {
        "statut": "calcule",
        "campagne": {"id": s.id, "nom": s.name, "date_debut": s.date_debut, "date_fin": s.date_fin} if s else None,
        "totaux": {
            "ecoles_actives": (await db.execute(q_ecoles)).scalar_one(),
            "classes_actives": (await db.execute(q_classes)).scalar_one(),
            "eleves_actifs": (await db.execute(q_eleves)).scalar_one(),
            "tuteurs_affectes": len(tuteurs),
            "rapports_recus": nb_r,
            "visites_recues": nb_v,
            "presences_individuelles": nb_p,
            "eleves_avec_appel": nb_eleves_appel,
        },
        "unites": {"ecoles_actives": "écoles", "classes_actives": "classes", "eleves_actifs": "élèves",
                   "tuteurs_affectes": "tuteurs", "rapports_recus": "rapports", "visites_recues": "visites",
                   "presences_individuelles": "élève × jour", "eleves_avec_appel": "élèves"},
        "fraicheur": fraicheur,
        "calendrier_configure": await camp.calendrier_configure(db, s.id) if s else False,
        "obligations_configurees": bool(await camp.obligations(db, s.id)) if s else False,
    }


# ── Mise en œuvre et soumissions ──────────────────────────────────────────────

async def soumissions(db: AsyncSession, f: Filtres) -> dict:
    s = await camp.session_ou_active(db, f.session_id)
    if s is None:
        return _non_calculable("Aucune campagne active ni choisie.")
    if not await camp.calendrier_configure(db, s.id):
        return _non_calculable("Calendrier attendu non configuré pour cette campagne (Paramètres de campagne).")
    obligations = await camp.obligations(db, s.id)
    if not obligations:
        return _non_calculable("Aucune obligation de soumission configurée pour cette campagne.")
    seuils = await camp.seuils(db, s.id)
    retard_tol = int(seuils["retard_transmission_jours"]["valeur"])
    debut, fin = _periode(s, f)
    jours = await camp.jours_attendus(db, s.id, debut, fin)
    ecoles = await _noms_ecoles(db)
    tuteurs = await _tuteurs(db, s, f)
    lignes: list[dict] = []
    regles = []

    for ob in obligations:
        regles.append({"role": ob.role, "objet": ob.objet, "periodicite": ob.periodicite, "quantite": ob.quantite,
                       "delai_jours": ob.delai_jours, "version": ob.version})
        if ob.role == "enseignant":
            acteurs = [(u, arr) for u, arr in tuteurs]
        else:
            acteurs = await _superviseurs(db, s, f, {u.id for u, _ in tuteurs})
        for u, arrivee in acteurs:
            limite = min(_fin_acteur(u, fin), date.today() - timedelta(days=ob.delai_jours))
            jours_u = [d for d in jours if arrivee <= d <= limite]
            attendu, recu, en_retard, derniere = await _compter(db, ob, u, s, jours_u, debut, fin, retard_tol)
            sc = ecoles.get(u.school_id)
            lignes.append({
                "acteur_id": u.id, "acteur": u.name, "role": ob.role, "objet": ob.objet,
                "ecole_id": u.school_id, "ecole": sc.name if sc else None,
                "region": sc.region if sc else None, "commune": sc.city if sc else None,
                "arrivee": arrivee, "attendu": attendu, "recu": recu, "manquant": max(attendu - recu, 0),
                "en_retard": en_retard, "derniere_soumission": derniere,
                "taux": round(100 * min(recu, attendu) / attendu, 1) if attendu else None,
                "sans_soumission": attendu > 0 and recu == 0,
            })
    totaux = {
        "acteurs": len({(l["acteur_id"], l["objet"]) for l in lignes}),
        "attendu": sum(l["attendu"] for l in lignes), "recu": sum(min(l["recu"], l["attendu"]) for l in lignes),
        "manquant": sum(l["manquant"] for l in lignes), "en_retard": sum(l["en_retard"] for l in lignes),
        "sans_soumission": sum(1 for l in lignes if l["sans_soumission"]),
    }
    totaux["taux"] = round(100 * totaux["recu"] / totaux["attendu"], 1) if totaux["attendu"] else None
    return {
        "statut": "calcule", "periode": {"debut": debut, "fin": fin, "jours_attendus": len(jours)},
        "regles": regles, "seuil_retard": seuils["retard_transmission_jours"],
        "unites": {"attendu": "soumissions", "recu": "soumissions (plafonnées à l'attendu par période)", "taux": "%"},
        "totaux": totaux, "lignes": sorted(lignes, key=lambda l: (l["taux"] if l["taux"] is not None else 101, l["acteur"])),
    }


async def _superviseurs(db: AsyncSession, s: ProgramSession, f: Filtres, tuteurs_campagne: set[uuid.UUID]) -> list[tuple[User, date]]:
    """Superviseurs dont au moins un tuteur supervisé est affecté à la campagne."""
    ecoles = await _ecoles(db, f)
    q = select(User).where(User.role == UserRole.superviseur, User.status == UserStatus.actif)
    if ecoles is not None:
        q = q.where(User.school_id.in_(ecoles))
    out = []
    for sup in (await db.execute(q.order_by(User.name))).scalars():
        if set(await get_supervised_teacher_ids(db, sup)) & tuteurs_campagne:
            out.append((sup, s.date_debut))
    return out


async def _compter(db, ob, u: User, s: ProgramSession, jours: list[date], debut: date, fin: date, retard_tol: int):
    """(attendu, reçu plafonné par période, en retard, dernière soumission)."""
    if ob.objet == "rapport_journalier":
        q = select(RapportJournalier.date_rapport, RapportJournalier.created_at).where(
            RapportJournalier.teacher_id == u.id, RAPPORT_COMPTE, RapportJournalier.date_rapport.between(debut, fin))
    elif ob.objet == "pointage":
        q = select(SupervisorPresenceCheck.date_jour, SupervisorPresenceCheck.created_at).where(
            SupervisorPresenceCheck.superviseur_id == u.id, SupervisorPresenceCheck.date_jour.between(debut, fin))
    else:
        col = Visite.superviseur_id if ob.role == "superviseur" else Visite.teacher_id
        q = select(Visite.date_visite, Visite.created_at).where(
            col == u.id, Visite.statut == "soumise", Visite.date_visite.between(debut, fin))
    rows = (await db.execute(q)).all()
    if ob.objet == "pointage":
        # un pointage = une journée pointée, quel que soit le nombre de tuteurs
        vus = {}
        for d, c in rows:
            vus[d] = min(vus.get(d, c), c)
        rows = list(vus.items())
    jours_set = set(jours)
    if ob.periodicite == "jour":
        attendu = ob.quantite * len(jours)
        par_jour = defaultdict(int)
        for d, _ in rows:
            if d in jours_set:
                par_jour[d] += 1
        recu = sum(min(n, ob.quantite) for n in par_jour.values())
    else:
        semaines = {camp.semaine_iso(d) for d in jours}
        attendu = ob.quantite * len(semaines)
        par_sem = defaultdict(int)
        for d, _ in rows:
            if camp.semaine_iso(d) in semaines:
                par_sem[camp.semaine_iso(d)] += 1
        recu = sum(min(n, ob.quantite) for n in par_sem.values())
    en_retard = sum(1 for d, c in rows if d in jours_set or ob.periodicite == "semaine"
                    if c and c.date() > d + timedelta(days=ob.delai_jours + retard_tol))
    derniere = max((c for _, c in rows if c), default=None)
    return attendu, recu, en_retard, derniere


async def relances(db: AsyncSession, f: Filtres) -> dict:
    """Liste de relance : acteurs sans soumission ou en dessous de l'attendu."""
    vue = await soumissions(db, f)
    if vue["statut"] != "calcule":
        return vue
    vue["lignes"] = [l for l in vue["lignes"] if l["manquant"] > 0]
    vue["totaux"] = {"a_relancer": len(vue["lignes"])}
    return vue


# ── Assiduité ─────────────────────────────────────────────────────────────────

async def assiduite(db: AsyncSession, f: Filtres, limite: int = 2000) -> dict:
    s = await camp.session_ou_active(db, f.session_id)
    if s is None:
        return _non_calculable("Aucune campagne active ni choisie.")
    debut, fin = _periode(s, f)
    seuils = await camp.seuils(db, s.id)
    cal = await camp.calendrier_configure(db, s.id)
    jours = await camp.jours_attendus(db, s.id, debut, fin) if cal else []
    ecoles = await _ecoles(db, f)
    q = (select(PresenceEleve, Eleve).join(Eleve, Eleve.id == PresenceEleve.eleve_id)
         .where(PresenceEleve.session_id == s.id, PresenceEleve.date_jour.between(debut, fin)))
    if ecoles is not None:
        q = q.where(Eleve.school_id.in_(ecoles))
    if f.teacher_id:
        q = q.where(PresenceEleve.teacher_id == f.teacher_id)
    rows = (await db.execute(q.order_by(PresenceEleve.date_jour))).all()
    if not rows:
        return _non_calculable(
            "Aucun appel individuel reçu sur la période : les rapports doivent transmettre la liste identifiée des absents.",
            calendrier_configure=cal)
    par_eleve: dict[uuid.UUID, dict] = {}
    for p, e in rows:
        d = par_eleve.setdefault(e.id, {"eleve": e, "jours": [], "teacher_id": p.teacher_id})
        d["jours"].append((p.date_jour, p.statut))
    noms_ecoles = await _noms_ecoles(db)
    taux_seuil = seuils["absences_taux"]["valeur"]
    consec_seuil = int(seuils["absences_consecutives"]["valeur"])
    jours_attendus = set(jours)
    lignes = []
    for eid, d in par_eleve.items():
        e: Eleve = d["eleve"]
        statuts = [st for _, st in d["jours"]]
        renseignes = len(statuts)
        absents = statuts.count("absent")
        excuses = statuts.count("excuse")
        serie = max_serie = 0
        for st in statuts:
            serie = serie + 1 if st == "absent" else 0
            max_serie = max(max_serie, serie)
        taux = round(100 * absents / renseignes, 1) if renseignes else None
        non_renseignes = len(jours_attendus - {j for j, _ in d["jours"]}) if cal else None
        sc = noms_ecoles.get(e.school_id)
        alerte = []
        if taux is not None and taux >= taux_seuil:
            alerte.append("taux")
        if max_serie >= consec_seuil:
            alerte.append("consecutives")
        lignes.append({
            "eleve_id": eid, "nom": f"{e.nom} {e.prenom or ''}".strip(), "classe": e.classe,
            "ecole_id": e.school_id, "ecole": sc.name if sc else None, "teacher_id": d["teacher_id"],
            "jours_renseignes": renseignes, "absences": absents, "absences_excusees": excuses,
            "jours_non_renseignes": non_renseignes, "jours_attendus": len(jours) if cal else None,
            "taux_absence": taux, "max_absences_consecutives": max_serie, "alertes": alerte,
        })
    lignes.sort(key=lambda l: (-(l["taux_absence"] or 0), l["nom"]))
    return {
        "statut": "calcule", "periode": {"debut": debut, "fin": fin, "jours_attendus": len(jours) if cal else None},
        "calendrier_configure": cal,
        "seuils": {"absences_taux": seuils["absences_taux"], "absences_consecutives": seuils["absences_consecutives"]},
        "unites": {"taux_absence": "% des jours d'appel renseignés", "absences": "jours", "jours_non_renseignes": "jours attendus sans appel"},
        "totaux": {
            "eleves": len(lignes), "jours_renseignes": sum(l["jours_renseignes"] for l in lignes),
            "absences": sum(l["absences"] for l in lignes),
            "eleves_en_alerte": sum(1 for l in lignes if l["alertes"]),
        },
        "lignes": lignes[:limite], "tronque": len(lignes) > limite,
    }


# ── Supervision et outils ─────────────────────────────────────────────────────

async def supervision(db: AsyncSession, f: Filtres) -> dict:
    s = await camp.session_ou_active(db, f.session_id)
    if s is None:
        return _non_calculable("Aucune campagne active ni choisie.")
    debut, fin = _periode(s, f)
    seuils = await camp.seuils(db, s.id)
    delai = int(seuils["visite_delai_jours"]["valeur"])
    taux_min = seuils["domaine_taux_min"]["valeur"]
    tuteurs = await _tuteurs(db, s, f)
    visites = (await db.execute(select(Visite).where(
        Visite.session_id == s.id, Visite.statut == "soumise", Visite.date_visite.between(debut, fin),
    ))).scalars().all()
    par_tuteur: dict[uuid.UUID, list[Visite]] = defaultdict(list)
    for v in visites:
        par_tuteur[v.teacher_id].append(v)
    noms = dict((await db.execute(select(User.id, User.name).where(User.role == UserRole.superviseur))).all())
    ecoles = await _noms_ecoles(db)
    lignes = []
    for u, _ in tuteurs:
        vs = sorted(par_tuteur.get(u.id, []), key=lambda v: v.date_visite)
        derniere = vs[-1] if vs else None
        depuis = (date.today() - derniere.date_visite).days if derniere else None
        dom: dict[str, list[float]] = defaultdict(list)
        for v in vs:
            for k, d in (v.scores_domaines or {}).items():
                if d.get("taux") is not None:
                    dom[k].append(d["taux"])
        moy = {k: round(sum(t) / len(t), 1) for k, t in dom.items()}
        sc = ecoles.get(u.school_id)
        lignes.append({
            "teacher_id": u.id, "tuteur": u.name, "classes": u.classes or [], "ecole_id": u.school_id,
            "ecole": sc.name if sc else None, "nb_visites": len(vs),
            "derniere_visite": derniere.date_visite if derniere else None,
            "dernier_superviseur": noms.get(derniere.superviseur_id) if derniere else None,
            "jours_depuis_visite": depuis, "sans_visite_recente": depuis is None or depuis > delai,
            "taux_domaines": moy, "domaines_a_appuyer": sorted(k for k, t in moy.items() if t < taux_min),
            "outils_indisponibles": derniere.nb_outils_indisponibles if derniere else None,
        })
    domaines_globaux: dict[str, list[float]] = defaultdict(list)
    for l in lignes:
        for k, t in l["taux_domaines"].items():
            domaines_globaux[k].append(t)
    # Items en difficulté : moyenne des valeurs / barème par item, toutes visites.
    items = {}
    if visites:
        from app.models.visite import VisiteReponse
        q = (select(GrilleVisiteItem.code, GrilleVisiteItem.libelle, GrilleVisiteItem.domaine, GrilleVisiteItem.type,
                    func.avg(VisiteReponse.valeur / func.nullif(GrilleVisiteItem.bareme_max, 0)),
                    func.count(VisiteReponse.id),
                    func.count().filter(VisiteReponse.disponible.is_(False)))
             .join(VisiteReponse, VisiteReponse.item_id == GrilleVisiteItem.id)
             .where(VisiteReponse.visite_id.in_([v.id for v in visites]), VisiteReponse.non_applicable.is_(False))
             .group_by(GrilleVisiteItem.code, GrilleVisiteItem.libelle, GrilleVisiteItem.domaine, GrilleVisiteItem.type))
        for code, lib, domaine, type_, moy, n, ko in (await db.execute(q)).all():
            items[code] = {"code": code, "libelle": lib, "domaine": domaine, "type": type_, "reponses": n,
                           "taux_moyen": round(100 * float(moy), 1) if moy is not None else None,
                           "indisponibles": ko if type_ == "outil" else None}
    return {
        "statut": "calcule", "periode": {"debut": debut, "fin": fin},
        "seuils": {"visite_delai_jours": seuils["visite_delai_jours"], "domaine_taux_min": seuils["domaine_taux_min"]},
        "unites": {"taux_domaines": "% du barème des items applicables renseignés", "jours_depuis_visite": "jours"},
        "totaux": {
            "tuteurs": len(lignes), "visites": len(visites),
            "tuteurs_visites": sum(1 for l in lignes if l["nb_visites"]),
            "sans_visite_recente": sum(1 for l in lignes if l["sans_visite_recente"]),
        },
        "domaines": {k: round(sum(t) / len(t), 1) for k, t in domaines_globaux.items()},
        "items": sorted(items.values(), key=lambda i: (i["taux_moyen"] if i["taux_moyen"] is not None else 101)),
        "lignes": sorted(lignes, key=lambda l: (not l["sans_visite_recente"], l["tuteur"])),
    }


# ── Difficultés et appui ──────────────────────────────────────────────────────

async def appui(db: AsyncSession, f: Filtres) -> dict:
    s = await camp.session_ou_active(db, f.session_id)
    ecoles = await _ecoles(db, f)
    q = select(DemandeAppui)
    if s is not None and f.session_id:
        q = q.where(DemandeAppui.session_id == s.id)
    if ecoles is not None:
        q = q.where(DemandeAppui.school_id.in_(ecoles))
    if f.teacher_id:
        q = q.where(DemandeAppui.beneficiaire_id == f.teacher_id)
    demandes = (await db.execute(q)).scalars().all()
    today = date.today()
    par_statut, par_origine, par_domaine = defaultdict(int), defaultdict(int), defaultdict(lambda: {"total": 0, "ouvertes": 0})
    delais = []
    for d in demandes:
        par_statut[d.statut] += 1
        par_origine[d.origine] += 1
        k = d.domaine or "Non précisé"
        par_domaine[k]["total"] += 1
        if d.statut in STATUTS_DEMANDE_OUVERTS:
            par_domaine[k]["ouvertes"] += 1
        if d.statut == "resolue" and d.cloturee_le:
            delais.append((d.cloturee_le - d.created_at).total_seconds() / 86400)
    ouvertes = [d for d in demandes if d.statut in STATUTS_DEMANDE_OUVERTS]
    # Besoins signalés dans les rapports et pas encore transformés en demande.
    q_b = select(func.count()).select_from(RapportJournalier).where(
        RAPPORT_COMPTE, RapportJournalier.besoin_appui.is_(True),
        ~select(DemandeAppui.id).where(DemandeAppui.rapport_id == RapportJournalier.id).exists())
    if s is not None and f.session_id:
        q_b = q_b.where(RapportJournalier.session_id == s.id)
    if ecoles is not None:
        q_b = q_b.where(RapportJournalier.ecole_id.in_(ecoles))
    return {
        "statut": "calcule",
        "unites": {"delai_moyen_resolution": "jours"},
        "totaux": {
            "demandes": len(demandes), "ouvertes": len(ouvertes),
            "en_retard": sum(1 for d in ouvertes if d.echeance and d.echeance < today),
            "sans_responsable": sum(1 for d in ouvertes if d.responsable_id is None),
            "resolues": par_statut.get("resolue", 0),
            "delai_moyen_resolution": round(sum(delais) / len(delais), 1) if delais else None,
            "besoins_rapports_non_traites": (await db.execute(q_b)).scalar_one(),
        },
        "par_statut": dict(par_statut), "par_origine": dict(par_origine),
        "par_domaine": sorted(({"domaine": k, **v} for k, v in par_domaine.items()), key=lambda x: -x["ouvertes"]),
        "lignes": [],
    }

