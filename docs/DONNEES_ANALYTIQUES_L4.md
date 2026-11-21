# Données analytiques — lot L4 (rapport V2, Partie II)

Ce document décrit la couche d'exploitation des données livrée en L4 : l'archive CSV analytique, l'API paginée, les indicateurs et ce qui change à la réception des rapports. Il complète le `LISEZMOI.txt` et le `manifest.json` placés dans chaque archive.

## 1. Mise en production

1. Sauvegarder la base (procédure L0).
2. Lancer `alembic upgrade head`. Migration `u9v0w1x2y3z4` :
   - sur `rapports_journalier`, ajoute `ecole_id`, `superviseur_id`, `absents_ids`, `effectif_attendu` et `submitted_at` (toutes ces colonnes acceptent NULL) ;
   - ajoute `rapport_questions.version` et crée la table `rapport_question_versions` ;
   - enregistre en version 1 toutes les questions existantes.
3. Le retour arrière est possible (`alembic downgrade t8u9v0w1x2y3`). Il supprime les nouvelles colonnes.
4. Les rapports antérieurs à L4 n'ont pas ces nouveaux champs : les cellules restent vides, jamais recalculées après coup. L'export le signale (`ecole_non_rattachee`, `superviseur_non_rattache`) dans `qualite_donnees.csv`, et les absents de ces rapports sont rapprochés par nom quand c'est possible (`methode_rattachement`), sinon signalés `absent_non_rapproche`.
5. Les applications mobiles qui n'ont pas encore la mise à jour continuent de fonctionner. Une saisie rejouée sans `absents_ids` ni `submitted_at` reste idempotente : elle garde le même hash.

## 2. Ce qui est figé à la réception d'un rapport (D5)

| Champ | Règle |
|---|---|
| `session_id` | Campagne active du tuteur (L3) |
| `ecole_id` | École du compte auteur au moment de la réception |
| `superviseur_id` | Superviseur à qui le tuteur est affecté explicitement. À défaut, l'unique superviseur de l'école qui n'a aucune affectation. Si l'auteur est lui-même superviseur, c'est lui. |
| `effectif_attendu` | Élèves actifs non archivés des classes du tuteur au moment de la réception |
| `absents_ids` | Identifiants des élèves déclarés absents, envoyés par le mobile. Tout identifiant inconnu ou hors périmètre est refusé (422 `ABSENTS_INCONNUS`). Il ne peut pas y en avoir plus que `nb_absences`. |
| `submitted_at` | Horodatage de l'appareil : obligatoirement avec fuseau et jamais dans le futur |

## 3. Accès (D6.2, D11)

- Administrateurs et coordonnateurs : indicateurs, export **pseudonymisé** et API.
- **Nominatif** : réservé au rôle `admin`, sinon 403. En mode nominatif, l'export contient en plus :
  - les noms ;
  - les textes libres (commentaires, absents saisis) ;
  - les identifiants réels des élèves ;
  - les fichiers `compat/` qui contiennent des noms.
- Chaque export est journalisé dans `audit_logs` (action `export`, entité `Analytique`). L'entrée enregistre :
  - l'identifiant d'export ;
  - le mode ;
  - les filtres ;
  - la borne du snapshot ;
  - le nombre de lignes par table ;
  - le nombre d'anomalies ;
  - la taille.

  L'admin consulte cet historique dans **Données → Centre d'exports**.
- En mode pseudonymisé, `eleve_id` vaut « E » suivi d'un HMAC tronqué (calculé avec `SECRET_KEY`). Cet identifiant est opaque et stable d'un export à l'autre, et il permet les jointures entre les tables du pack. **Changer `SECRET_KEY` change ces identifiants.**

## 4. Points d'accès (`/api/v1/admin/analytique`)

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/indicateurs` | Indicateurs D7, calculés sur le même snapshot que l'archive |
| GET | `/export` | Archive ZIP complète du périmètre filtré. L'en-tête `X-Export-Id` porte l'identifiant (`PSEUDO-…` ou `NOMIN-…`). |
| GET | `/tables` | Catalogue versionné : tables, clés, colonnes, statut de collecte |
| GET | `/tables/{table}` | Lecture paginée (`limit` ≤ 2000, `curseur`). Pour une reprise incrémentale, passer `updated_since=<borne_snapshot précédente>`. |
| GET | `/dictionnaire` | Dictionnaire des variables |
| GET | `/exports` | Historique des exports |

Filtres communs aux points d'accès :

| Filtre | Signification |
|---|---|
| `campagne_id` | Campagne |
| `date_debut`, `date_fin` | Période d'activité |
| `recu_depuis`, `recu_jusqua` | Période de réception par le serveur (données tardives) |
| `ief`, `commune`, `ecole_id`, `tuteur_id`, `superviseur_id` | Périmètre géographique et acteurs |
| `statut` | `tous`, `valides`, `soumis`, `corrige` ou `annule` |
| `nominatif` | Mode nominatif (admin seulement) |

**Cohérence.** Chaque lecture se fait dans une transaction `REPEATABLE READ READ ONLY`. La borne `borne_snapshot` (`now()` du serveur) indique jusqu'où les données sont prises en compte.

**Reprise incrémentale.** Elle inclut les annulations : la ligne revient avec `statut_enregistrement = annule` (REC14). Les tables dérivées d'un rapport suivent le `updated_at` de ce rapport. `dictionnaire_codes` ne se lit qu'en snapshot complet (422 sinon).

## 5. Contenu de l'archive

```
tables/            une table par unité d'analyse (voir dictionnaire.csv)
qualite_donnees.csv  anomalies signalées (aucune ligne supprimée)
compat/            Baseglobale.csv, ElevesAbsents.csv, difficulte.csv, BaseBesoins.csv,
                   Commentaires.csv (nominatif seulement)
dictionnaire.csv   variable, table, type, définition, valeurs, nature (nominatif ?)
manifest.json      filtres, borne, conventions, nombre de lignes par fichier, bilan qualité, limites
LISEZMOI.txt       mode d'emploi lisible
```

Tables : `campagnes`, `ecoles`, `classes`, `acteurs`, `affectations`, `eleves`, `inscriptions`, `planning`, `rapports_journaliers`, `absences_eleves`, `difficultes_signalees`, `besoins_appui`, `commentaires`, `reponses_questions`, `seances`, `pointages`, `evaluations`, `mouvements_eleves`, `dictionnaire_codes`.

Ces modules **ne sont pas collectés** par l'application : `presences_eleves`, `calendrier_attendu`, `visites_supervision` et `items_supervision`. Leur fichier est fourni vide, avec les en-têtes et le statut `non_collecte` dans le manifeste. Ils ne sont jamais remplacés par une approximation.

### Conventions (D6.3)

- UTF-8 sans BOM, séparateur virgule, guillemets RFC 4180. Un texte avec virgule, guillemet ou saut de ligne tient dans une seule cellule.
- Booléens : `1` ou `0`. Cellule vide = non renseigné (**jamais 0**).
- Dates d'activité : `AAAA-MM-JJ` (Africa/Dakar). Horodatages : ISO 8601 avec fuseau.
- Formules : un texte qui commence par `=`, `+`, `-`, `@` ou une tabulation, et qui n'est pas un nombre, est préfixé d'une apostrophe. `-3` reste un nombre.

### Évaluations unifiées (APP-36, REC12)

`evaluations.csv` réunit plusieurs sources dans une même table :

| Source | Contenu |
|---|---|
| `competence` | Observations par compétence |
| `sujet` | Tirages de sujets |
| `sujet_historique` | Réévaluations remplacées (`valide = 0`, colonne `remplace_le`) |

`resultat_harmonise` ramène les deux échelles à une seule :

| Échelle des sujets | Valeur harmonisée |
|---|---|
| réussi | acquis |
| intermédiaire | en_cours |
| pas réussi | a_aider |

Le code d'origine reste conservé dans `resultat_code`.

### Questions versionnées (REC13)

Modifier le libellé, le type, les options ou la cible d'une question crée une nouvelle version. Changer l'ordre ou l'état actif n'en crée pas. Chaque réponse porte la version en vigueur à la réception du rapport, et `dictionnaire_codes.csv` liste toutes les versions (`<question_id>@<version>`).

## 6. Indicateurs (D7) — définitions proposées, à valider par ARED

Règles communes : les rapports annulés sont exclus ; si le dénominateur vaut 0, la valeur est vide et non 0 %.

| Code | Calcul | Statut actuel |
|---|---|---|
| `eleves_beneficiaires_uniques` | Nombre distinct d'`eleve_id` ayant une inscription active | calculé |
| `rapports_valides` | Rapports non annulés | calculé |
| `taux_rapports_recus` | Rapports reçus / rapports attendus | **non calculable** : pas de calendrier attendu |
| `rapports_avec_difficulte` | Rapports distincts avec au moins une difficulté autre que « Aucune », divisés par les rapports non annulés (REC02) | calculé |
| `difficultes_resolues` | Résolutions enregistrées / signalements | calculé |
| `absences_declarees` | Somme de `nb_absences_declarees` | calculé |
| `taux_presence_observee`, `couverture_appels` | — | **non calculable** : pas d'appel individuel |
| `demandes_appui` | Rapports avec besoin d'appui | calculé (aucun suivi du traitement) |
| `eleves_evalues` | Élèves distincts, toutes sources confondues ; historique exclu | calculé |
| `duree_pedagogique_observee` | Séances clôturées par le tuteur | calculé |
| `tuteurs_presents_pointes` | Tuteurs distincts pointés présents | calculé |
| `visites_pedagogiques`, `score_supervision` | — | **non calculable** : pas de grille de visite |

Chaque indicateur se recalcule à l'identique depuis les CSV du pack, puisqu'il est calculé sur le même snapshot avec les mêmes builders (REC16, testé).

## 7. Décisions ARED encore ouvertes (Annexe D)

| Sujet | Effet tant que la décision n'est pas prise |
|---|---|
| Calendrier des rapports attendus | `taux_rapports_recus` reste non calculable |
| Appel individuel des élèves | `taux_presence_observee` et `couverture_appels` restent non calculables |
| Grille de visite de supervision | `visites_pedagogiques` et `score_supervision` restent non calculables |
| Affectations datées tuteur ↔ superviseur | Le superviseur est figé à la réception ; les affectations de supervision sont exportées avec `datee = 0` |
| Circuit de traitement des demandes d'appui | `statut_traitement = non_suivi` |
| Classe portée par chaque rapport | Pour l'instant, celle des classes du tuteur |
| Validation des définitions D7 | Par campagne |

## 8. Tests

- `tests/integration/test_l4_donnees.py` couvre :
  - la réception (rattachements, absents) ;
  - les scénarios REC02, REC03, REC12, REC13, REC14, REC16 et REC17 ;
  - l'accès nominatif ;
  - le manifeste ;
  - l'export CSV D3.3.
- `tests/integration/test_l4_eleves_pagination.py` couvre APP-38 avec 10 050 élèves.
