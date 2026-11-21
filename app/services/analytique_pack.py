"""Archive CSV analytique (rapport V2, lot L4 — Partie II, D6).

Contenu du zip :
- `tables/<table>.csv`      une table par fichier (D5), tables vides avec en-tête ;
- `compat/*.csv`            sorties de transition des anciens scripts (D6.6) ;
- `dictionnaire.csv`        une ligne par variable (D6.4) ;
- `qualite_donnees.csv`     anomalies et rapprochements non résolus (D10) ;
- `manifest.json`           manifeste structuré ; `LISEZMOI.txt` lisible.

Conventions (documentées dans le manifeste) : UTF-8 sans BOM, séparateur
virgule, guillemets doubles RFC 4180 (un texte sur plusieurs lignes reste une
seule cellule, REC03), booléens 1/0, valeur manquante = cellule vide (jamais
0), dates AAAA-MM-JJ, horodatages ISO 8601 avec fuseau, identifiants en texte.
Neutralisation des formules : une cellule TEXTE commençant par = + - @ ou une
tabulation, et qui n'est pas un nombre, est préfixée d'une apostrophe ; les
vrais nombres ne sont jamais transformés en texte.

Cohérence (D6.5) : toutes les tables sont lues dans UNE transaction
REPEATABLE READ en lecture seule ; la borne du snapshot est l'instant de début
de cette transaction. Une correction arrivant pendant la génération n'y figure
pas, et figurera dans l'export suivant.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services.analytique_tables import (
    FUSEAU, SCHEMA_VERSION, TABLES, Col, Contexte, Filtres, Table,
)

_NOMBRE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")
_DECLENCHEURS = ("=", "+", "-", "@", "\t", "\r")


def cellule(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    s = str(v)
    if s.startswith(_DECLENCHEURS) and not _NOMBRE.match(s.strip()):
        return "'" + s
    return s


def ecrire_csv(colonnes: list[str], lignes: Iterable[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(colonnes)
    for ligne in lignes:
        w.writerow([cellule(ligne.get(c)) for c in colonnes])
    return buf.getvalue()


# ── Sorties de transition (D6.6) ─────────────────────────────────────────────
# Noms des anciennes variables (Annexe A). Une variable non collectée est
# présente mais VIDE et déclarée « indisponible » dans le manifeste.

COMPAT_INDISPONIBLES = {
    "Baseglobale.csv": ["nb_total", "nb_garcon", "nb_fille", "nb_garcon_abs", "nb_fille_abs",
                        "presence_superviseur", "test_eleves", "horaires", "CodeTuteur"],
}


def compat(tables: dict[str, list[dict]], nominatif: bool) -> dict[str, tuple[list[str], list[dict]]]:
    rapports = {r["rapport_id"]: r for r in tables["rapports_journaliers"]}
    valides = [r for r in tables["rapports_journaliers"] if r["statut_enregistrement"] != "annule"]
    diffs: dict[Any, list[str]] = {}
    for d in tables["difficultes_signalees"]:
        diffs.setdefault(d["rapport_id"], []).append(d["difficulte_libelle"])
    besoins: dict[Any, list[str]] = {}
    for b in tables["besoins_appui"]:
        besoins.setdefault(b["rapport_id"], []).append(b["domaine_appui_libelle"])
    com = {c["rapport_id"]: c.get("texte") for c in tables["commentaires"] if c["categorie"] == "commentaire"}

    base_cols = ["rapport_id", "campagne_id", "ecole_id", "auteur_id", "superviseur_id", "date", "ief",
                 "commune", "ecole", "superviseur", "tuteur", "CodeTuteur", "horaires",
                 "nb_total", "nb_garcon", "nb_fille", "nb_abs", "nb_garcon_abs", "nb_fille_abs",
                 "effectif_attendu", "semaine", "jour", "presence_superviseur", "test_eleves",
                 "directeur_venu", "difficultes", "besoin_appui", "domaines_appui", "autres_commentaires"]
    base = [{
        "rapport_id": r["rapport_id"], "campagne_id": r["campagne_id"], "ecole_id": r["ecole_id"],
        "auteur_id": r["auteur_id"], "superviseur_id": r["superviseur_id"], "date": r["date_activite"],
        "ief": r["ief_libelle"], "commune": r["commune_libelle"], "ecole": r["ecole_libelle"],
        "superviseur": r.get("superviseur_libelle") if nominatif else None,
        "tuteur": r.get("nom_tuteur") if nominatif else None,
        "nb_abs": r["nb_absences_declarees"], "effectif_attendu": r["effectif_attendu"],
        "semaine": r["semaine_programme"], "jour": r["jour_programme"],
        "directeur_venu": r["directeur_present_declare"],
        "difficultes": " | ".join(diffs.get(r["rapport_id"], [])) or None,
        "besoin_appui": r["besoin_appui_declare"],
        "domaines_appui": " | ".join(besoins.get(r["rapport_id"], [])) or None,
        "autres_commentaires": com.get(r["rapport_id"]) if nominatif else None,
    } for r in valides]

    def valide(rid) -> bool:
        return rapports.get(rid, {}).get("statut_enregistrement") != "annule"

    abs_cols = ["absence_id", "rapport_id", "date", "campagne_id", "ecole_id", "auteur_id", "eleve_id",
                "methode_rattachement"] + (["NomEleve"] if nominatif else [])
    absents = [{**a, "date": a["date_activite"], "NomEleve": a.get("nom_saisi")}
               for a in tables["absences_eleves"] if valide(a["rapport_id"])]
    dif_cols = ["signalement_id", "rapport_id", "date", "difficulte", "difficultes_label", "statut_traitement"]
    dif = [{**d, "date": d["date_activite"], "difficulte": d["difficulte_code"],
            "difficultes_label": d["difficulte_libelle"]}
           for d in tables["difficultes_signalees"] if valide(d["rapport_id"])]
    bes_cols = ["besoin_id", "rapport_id", "date", "domaines_appui", "statut_traitement"]
    bes = [{**b, "date": b["date_activite"], "domaines_appui": b["domaine_appui_libelle"]}
           for b in tables["besoins_appui"] if valide(b["rapport_id"])]
    sortie = {
        "Baseglobale.csv": (base_cols, base),
        "ElevesAbsents.csv": (abs_cols, absents),
        "difficulte.csv": (dif_cols, dif),
        "BaseBesoins.csv": (bes_cols, bes),
    }
    if nominatif:
        com_cols = ["commentaire_id", "rapport_id", "date", "categorie", "texte"]
        sortie["Commentaires.csv"] = (com_cols, [{**c, "date": c["date_activite"]} for c in tables["commentaires"]
                                                if valide(c["rapport_id"])])
    return sortie


# ── Génération ───────────────────────────────────────────────────────────────

DICO_COLS = ["table", "variable", "libelle", "type", "valeurs_permises", "valeurs_manquantes",
             "nature", "source", "nominatif", "definition_table", "cle_table"]


def dictionnaire(nominatif: bool) -> list[dict]:
    rows = []
    for t in TABLES:
        for c in t.colonnes_pour(nominatif):
            rows.append({
                "table": t.nom, "variable": c.nom, "libelle": c.definition, "type": c.type,
                "valeurs_permises": c.valeurs or None,
                "valeurs_manquantes": "cellule vide = non renseigné / non applicable (jamais 0)",
                "nature": c.nature, "source": "application Ndaw Wune" if t.statut == "disponible" else "non collecté",
                "nominatif": c.nominatif, "definition_table": t.unite, "cle_table": t.cle,
            })
    for nom, cols in (("qualite_donnees", ["anomalie_id", "table_source", "ligne_id", "controle", "detail", "gravite"]),):
        for c in cols:
            rows.append({"table": nom, "variable": c, "libelle": c, "type": "texte", "nature": "derive",
                         "source": "contrôles du pack", "nominatif": False,
                         "definition_table": "Une anomalie ou un rapprochement non résolu", "cle_table": "anomalie_id"})
    return rows


async def collecter(ctx: Contexte) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in TABLES:
        out[t.nom] = await t.builder(ctx) if t.builder else []
    return out


async def snapshot(filtres: Filtres) -> tuple[dict[str, list[dict]], Contexte, datetime]:
    """Toutes les tables dans une transaction REPEATABLE READ en lecture seule."""
    async with AsyncSessionLocal() as s:
        await s.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        await s.execute(text("SET TRANSACTION READ ONLY"))
        borne = (await s.execute(text("SELECT now()"))).scalar_one()
        ctx = Contexte(s, filtres)
        tables = await collecter(ctx)
        await s.rollback()
    return tables, ctx, borne


def construire_zip(tables: dict[str, list[dict]], ctx: Contexte, borne: datetime, export_id: str) -> tuple[bytes, dict]:
    f = ctx.f
    buf = io.BytesIO()
    fichiers = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in TABLES:
            cols = [c.nom for c in t.colonnes_pour(f.nominatif)]
            lignes = tables[t.nom]
            z.writestr(f"tables/{t.nom}.csv", ecrire_csv(cols, lignes))
            fichiers.append({"fichier": f"tables/{t.nom}.csv", "table": t.nom, "lignes": len(lignes),
                             "cle": t.cle, "unite": t.unite, "statut": t.statut if t.statut != "disponible"
                             else ("vide" if not lignes else "disponible"), "note": t.note or None})
        anomalies = [{"anomalie_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{a.table_source}|{a.ligne_id}|{a.controle}|{i}")),
                      "table_source": a.table_source, "ligne_id": a.ligne_id, "controle": a.controle,
                      "detail": a.detail, "gravite": a.gravite} for i, a in enumerate(ctx.anomalies)]
        z.writestr("qualite_donnees.csv", ecrire_csv(
            ["anomalie_id", "table_source", "ligne_id", "controle", "detail", "gravite"], anomalies))
        fichiers.append({"fichier": "qualite_donnees.csv", "table": "qualite_donnees", "lignes": len(anomalies),
                         "cle": "anomalie_id", "unite": "Une anomalie ou un rapprochement non résolu",
                         "statut": "disponible" if anomalies else "vide"})
        for nom, (cols, lignes) in compat(tables, f.nominatif).items():
            z.writestr(f"compat/{nom}", ecrire_csv(cols, lignes))
            fichiers.append({"fichier": f"compat/{nom}", "table": nom, "lignes": len(lignes),
                             "statut": "transition", "variables_indisponibles": COMPAT_INDISPONIBLES.get(nom, [])})
        dico = dictionnaire(f.nominatif)
        z.writestr("dictionnaire.csv", ecrire_csv(DICO_COLS, dico))

        rapports = tables["rapports_journaliers"]
        controles: dict[str, int] = {}
        for a in ctx.anomalies:
            controles[a.controle] = controles.get(a.controle, 0) + 1
        manifeste = {
            "export_id": export_id,
            "mode": "nominatif" if f.nominatif else "pseudonymise",
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "fuseau": FUSEAU,
            "borne_snapshot": borne.isoformat(),
            "filtres": f.en_dict(),
            "encodage": "UTF-8 (sans BOM)", "separateur": ",", "guillemets": "RFC 4180",
            "conventions": {
                "booleens": "1 = vrai, 0 = faux, vide = non renseigné",
                "manquant": "cellule vide (jamais 0)",
                "dates": "AAAA-MM-JJ (date d'activité, Africa/Dakar)",
                "horodatages": "ISO 8601 avec fuseau",
                "identifiants": "texte ; eleve_id opaque et stable en mode pseudonymisé",
                "formules": "texte commençant par = + - @ ou tabulation, non numérique : préfixé d'une apostrophe",
            },
            "fichiers": fichiers,
            "bilan_qualite": {
                "rapports_exportes": len(rapports),
                "rapports_annules": sum(1 for r in rapports if r["statut_enregistrement"] == "annule"),
                "rapports_a_verifier": sum(1 for r in rapports if r["statut_qualite"] == "a_verifier"),
                "anomalies": len(anomalies),
                "anomalies_par_controle": controles,
                "lignes_exclues": "aucune : les anomalies sont signalées, jamais supprimées",
            },
            "limites": [t.note for t in TABLES if t.note],
        }
        z.writestr("manifest.json", json.dumps(manifeste, ensure_ascii=False, indent=2, default=str))
        z.writestr("LISEZMOI.txt", lisezmoi(manifeste))
    return buf.getvalue(), manifeste


def lisezmoi(m: dict) -> str:
    l = [
        "Ndaw Wune — archive analytique", "",
        f"Export : {m['export_id']} ({m['mode']})",
        f"Généré le : {m['generated_at']} — borne du snapshot : {m['borne_snapshot']} ({m['fuseau']})",
        f"Version du schéma : {m['schema_version']}",
        f"Filtres : {json.dumps(m['filtres'], ensure_ascii=False) if m['filtres'] else 'aucun'}", "",
        "Fichiers :",
    ]
    for f in m["fichiers"]:
        l.append(f"  - {f['fichier']} : {f['lignes']} ligne(s) [{f['statut']}]" + (f" — {f['note']}" if f.get("note") else ""))
    l += ["", "Conventions :"] + [f"  - {k} : {v}" for k, v in m["conventions"].items()]
    l += ["", "Limites connues :"] + [f"  - {x}" for x in m["limites"]]
    l += ["", "Le dictionnaire des variables est dans dictionnaire.csv ; les anomalies dans qualite_donnees.csv.",
          "Les tables détaillées (tables/) font référence ; compat/ sert à la transition des anciens scripts."]
    return "\n".join(l) + "\n"


def colonnes_table(t: Table, nominatif: bool) -> list[Col]:
    return t.colonnes_pour(nominatif)
