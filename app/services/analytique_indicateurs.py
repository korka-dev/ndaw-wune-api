"""Indicateurs unifiés (rapport V2, APP-36 et Partie II D7).

Calculés à partir des MÊMES lignes que l'archive analytique (même snapshot,
mêmes filtres) : chaque valeur se recalcule à l'identique depuis le pack
(REC16). Chaque indicateur annonce son unité, son numérateur, son
dénominateur, la population, les manquants et la période. Un indicateur dont
les données ne sont pas collectées est renvoyé « non_calculable » avec la
raison, jamais approché par une autre mesure (D4.4, D6.6).

Les définitions sont celles PROPOSÉES par le rapport (D7) ; ARED les valide
par campagne (Annexe D). Les rapports annulés sont exclus de tous les calculs.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.analytique_tables import AUCUNE, AUTRE


def _pct(num: int, den: int) -> Optional[float]:
    return round(100 * num / den, 1) if den else None


def ind(code: str, libelle: str, unite: str, valeur: Any, *, definition: str, sources: list[str],
        numerateur: Any = None, denominateur: Any = None, population: Optional[str] = None,
        manquants: Any = None, detail: Optional[dict] = None, statut: str = "calcule",
        raison: Optional[str] = None) -> dict:
    return {
        "code": code, "libelle": libelle, "unite": unite, "valeur": valeur, "statut": statut, "raison": raison,
        "numerateur": numerateur, "denominateur": denominateur, "population": population,
        "manquants": manquants, "definition": definition, "sources": sources, "detail": detail or {},
    }


def non_calculable(code: str, libelle: str, unite: str, raison: str, definition: str) -> dict:
    return ind(code, libelle, unite, None, definition=definition, sources=[], statut="non_calculable", raison=raison)


def calculer(tables: dict[str, list[dict]]) -> list[dict]:
    out: list[dict] = []
    rapports = [r for r in tables["rapports_journaliers"] if r["statut_enregistrement"] != "annule"]
    ids_valides = {r["rapport_id"] for r in rapports}
    annules = len(tables["rapports_journaliers"]) - len(rapports)

    # ── Élèves bénéficiaires uniques (inscriptions éligibles) ──
    insc = tables["inscriptions"]
    actives = [i for i in insc if i["statut_inscription"] == "active"]
    titulaires = {i["eleve_id"] for i in actives if (i["statut_selection"] or "").lower() != "remplaçant"}
    remplacants = {i["eleve_id"] for i in actives if (i["statut_selection"] or "").lower() == "remplaçant"}
    sortis = {i["eleve_id"] for i in insc if i["statut_inscription"] == "sortie"}
    out.append(ind(
        "eleves_beneficiaires_uniques", "Élèves bénéficiaires uniques", "enfants distincts",
        len({i["eleve_id"] for i in actives}),
        definition="Nombre distinct d'eleve_id ayant une inscription active dans le périmètre. "
                   "N'additionne jamais les effectifs des rapports.",
        sources=["inscriptions"], population="inscriptions actives du périmètre filtré",
        detail={"inscriptions": len(insc), "titulaires": len(titulaires), "remplacants": len(remplacants),
                "sortis": len(sortis)},
    ))

    # ── Rapports ──
    out.append(ind(
        "rapports_valides", "Rapports journaliers valides", "rapports", len(rapports),
        definition="Rapports soumis ou corrigés ; un rapport corrigé compte une fois, un rapport annulé ne compte pas.",
        sources=["rapports_journaliers"], population="rapports reçus dans le périmètre",
        detail={"annules_exclus": annules,
                "corriges": sum(1 for r in rapports if r["statut_enregistrement"] == "corrige"),
                "par_type": {t: sum(1 for r in rapports if r["type_rapport"] == t) for t in ("tuteur", "superviseur")},
                "recus_hors_ligne": sum(1 for r in rapports if r["soumis_hors_ligne"])},
    ))
    out.append(non_calculable(
        "taux_rapports_recus", "Taux de rapports reçus", "% d'obligations couvertes",
        "Le calendrier des obligations (jours attendus, objectifs par rôle, exceptions) n'est pas encore "
        "configuré : décision ARED (Annexe D, « Calendrier et soumissions »).",
        "Obligations attendues couvertes par un rapport valide / obligations attendues échues.",
    ))

    # ── Difficultés (REC02) ──
    par_rapport: dict[Any, set] = {}
    for d in tables["difficultes_signalees"]:
        if d["rapport_id"] in ids_valides:
            par_rapport.setdefault(d["rapport_id"], set()).add(d["difficulte_code"] or d["difficulte_libelle"])
    renseignes = len(par_rapport)
    avec = sum(1 for codes in par_rapport.values() if any(c != AUCUNE for c in codes))
    out.append(ind(
        "rapports_avec_difficulte", "Rapports avec au moins une difficulté", "% de rapports",
        _pct(avec, renseignes), numerateur=avec, denominateur=renseignes,
        manquants=len(rapports) - renseignes,
        definition="Rapports distincts citant au moins une difficulté réelle / rapports valides où la question est "
                   "renseignée. « Aucune » reste au dénominateur ; un rapport ne compte qu'une fois quel que soit "
                   "le nombre de difficultés.",
        sources=["difficultes_signalees", "rapports_journaliers"], population="rapports valides renseignés",
        detail={"signalements": sum(len([c for c in v if c != AUCUNE]) for v in par_rapport.values()),
                "autres": sum(1 for v in par_rapport.values() if AUTRE in v)},
    ))
    signalees = [d for d in tables["difficultes_signalees"]
                 if d["rapport_id"] in ids_valides and d["difficulte_code"] != AUCUNE]
    resolues = sum(1 for d in signalees if d["statut_traitement"] == "resolue")
    out.append(ind(
        "difficultes_resolues", "Difficultés résolues", "% de signalements", _pct(resolues, len(signalees)),
        numerateur=resolues, denominateur=len(signalees),
        definition="Signalements marqués résolus / signalements de difficultés réelles des rapports valides.",
        sources=["difficultes_signalees"], population="signalements (hors « Aucune »)",
        detail={"en_attente": len(signalees) - resolues},
    ))

    # ── Absences et présence ──
    declarees = sum(r["nb_absences_declarees"] for r in rapports)
    identifiees = [a for a in tables["absences_eleves"] if a["rapport_id"] in ids_valides]
    rapprochees = sum(1 for a in identifiees if a["eleve_id"])
    out.append(ind(
        "absences_declarees", "Absences déclarées", "absences élève-jour", declarees,
        definition="Somme des absences déclarées dans les rapports valides (un même enfant absent deux jours compte deux fois).",
        sources=["rapports_journaliers"], population="rapports valides",
        detail={"absences_identifiees": len(identifiees), "absences_rattachees_a_un_eleve": rapprochees,
                "eleves_distincts_absents": len({a["eleve_id"] for a in identifiees if a["eleve_id"]}),
                "couverture_identification_pct": _pct(rapprochees, declarees)},
    ))
    out.append(non_calculable(
        "taux_presence_observee", "Taux de présence observée", "% des statuts renseignés",
        "Les présents ne sont pas saisis individuellement (seules les absences le sont). Un élève non signalé "
        "absent n'est pas un présent observé : le taux n'est pas approché.",
        "Présences renseignées / (présences + absences renseignées) × 100.",
    ))
    out.append(non_calculable(
        "couverture_appels", "Couverture des appels", "% des appels attendus",
        "Pas d'appel individuel ni de calendrier attendu (voir taux_presence_observee et taux_rapports_recus).",
        "Appels élève avec statut exploitable / appels élève attendus sur activités réalisées.",
    ))

    # ── Appui ──
    besoins = [b for b in tables["besoins_appui"] if b["rapport_id"] in ids_valides]
    out.append(ind(
        "demandes_appui", "Demandes d'appui", "demandes (domaines)", len(besoins),
        definition="Une demande = un domaine d'appui cité dans un rapport valide.",
        sources=["besoins_appui"], population="rapports valides",
        detail={"rapports_exprimant_un_besoin": len({b["rapport_id"] for b in besoins}),
                "resolution": "non suivie (aucun circuit de traitement des demandes)"},
    ))

    # ── Évaluations (APP-36 : deux sources unifiées) ──
    evals = [e for e in tables["evaluations"] if e["valide"] and e["statut_passation"] != "absent"]
    par_source: dict[str, set] = {}
    par_instrument: dict[str, set] = {}
    for e in evals:
        par_source.setdefault(e["source"], set()).add(e["eleve_id"])
        par_instrument.setdefault(e["instrument_id"], set()).add(e["eleve_id"])
    dernier: dict[tuple, dict] = {}
    for e in sorted(evals, key=lambda x: (str(x["date_evaluation"] or ""), str(x["created_at"] or ""))):
        dernier[(e["eleve_id"], e["instrument_id"])] = e
    repartition: dict[str, int] = {}
    for e in dernier.values():
        k = e["resultat_harmonise"] or "non_harmonise"
        repartition[k] = repartition.get(k, 0) + 1
    out.append(ind(
        "eleves_evalues", "Élèves évalués", "enfants distincts", len({e["eleve_id"] for e in evals}),
        definition="Enfants distincts avec au moins une observation valide (passation effectuée), toutes sources "
                   "confondues ; source et instrument conservés. Les états remplacés par une réévaluation sont exclus.",
        sources=["evaluations"], population="observations valides du périmètre",
        detail={"observations": len(evals),
                "observations_historiques_exclues": sum(1 for e in tables["evaluations"] if not e["valide"]),
                "absents_a_la_passation": sum(1 for e in tables["evaluations"] if e["statut_passation"] == "absent"),
                "eleves_par_source": {k: len(v) for k, v in par_source.items()},
                "eleves_par_instrument": {k: len(v) for k, v in par_instrument.items()},
                "dernier_resultat_par_eleve_et_instrument": repartition},
    ))

    # ── Séances : durée observée séparée des clôtures sans mesure ──
    seances = tables["seances"]
    mesurees = [s["duree_observee_minutes"] for s in seances if s["duree_observee_minutes"] is not None]
    out.append(ind(
        "duree_pedagogique_observee", "Durée pédagogique observée", "minutes (moyenne par séance mesurée)",
        round(sum(mesurees) / len(mesurees), 1) if mesurees else None,
        numerateur=sum(mesurees), denominateur=len(mesurees),
        manquants=sum(1 for s in seances if s["statut"] == "terminee" and s["duree_observee_minutes"] is None),
        definition="Moyenne des durées mesurées des séances terminées par le tuteur (pauses déduites). Les "
                   "clôtures automatiques et récupérations n'ont pas de durée et ne sont jamais estimées.",
        sources=["seances"], population="séances du périmètre",
        detail={"seances": len(seances), "mesurees": len(mesurees),
                "cloturees_automatiquement": sum(1 for s in seances if s["cloture_mode"] == "automatique"),
                "recuperees": sum(1 for s in seances if s["cloture_mode"] == "recuperation"),
                "manquees": sum(1 for s in seances if s["statut"] == "manquee"),
                "en_cours": sum(1 for s in seances if s["statut"] == "en_cours")},
    ))

    # ── Supervision ──
    pts = tables["pointages_tuteurs"]
    presents = sum(1 for p in pts if p["present"])
    out.append(ind(
        "tuteurs_presents_pointes", "Tuteurs présents lors des pointages", "% des pointages",
        _pct(presents, len(pts)), numerateur=presents, denominateur=len(pts),
        definition="Pointages « présent » / pointages réalisés par les superviseurs (≠ visite pédagogique, "
                   "≠ présence du superviseur déclarée par le tuteur).",
        sources=["pointages_tuteurs"], population="pointages du périmètre",
        detail={"tuteurs_pointes": len({p["tuteur_id"] for p in pts})},
    ))
    out.append(non_calculable(
        "visites_pedagogiques", "Visites pédagogiques", "visites",
        "Le module de visite (grille d'items versionnée) n'existe pas encore : à définir avec ARED (Annexe D).",
        "Nombre de visites validées, distinct des pointages et de la présence déclarée.",
    ))
    out.append(non_calculable(
        "score_supervision", "Score de supervision", "% d'items conformes",
        "Pas de grille de supervision collectée (voir visites_pedagogiques).",
        "Items conformes / items applicables et renseignés × 100.",
    ))
    return out


def fraicheur(tables: dict[str, list[dict]]) -> Optional[str]:
    dates = [r["received_at"] for r in tables["rapports_journaliers"] if r["received_at"]]
    dates += [s["created_at"] for s in tables["seances"] if s["created_at"]]
    return max(dates).isoformat() if dates else None
