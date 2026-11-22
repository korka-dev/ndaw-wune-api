# Lot L6 — Modules de la Partie II (rapport V2 : D8, D9.3, D9.4, D10.2, APP-14, APP-33)

Ce document décrit ce que le lot L6 ajoute, les valeurs par défaut posées en
attendant la validation d'ARED, et la mise en production. Il ne contient aucun
secret ni aucune donnée nominative.

Branches `lot-L6-modules` (créées depuis L5) dans les trois dépôts.
Migration : `w1x2y3z4a5b6` (révise `v0w1x2y3z4a5`). Mobile : version **1.2.0**
(nouvel APK obligatoire : SQLCipher est un module natif).

## 1. Ce qui est livré

| Section | API | Dashboard | Mobile |
|---|---|---|---|
| Calendrier attendu, obligations, seuils (D8) | `/admin/campagnes/{id}/parametres`, `…/calendrier/generer` (aperçu puis application), `PATCH …/calendrier/{date}`, `PUT …/obligations` (versionnées), `PUT …/seuils`, `/admin/seuils-alerte` | Paramètres de campagne | — |
| Appel individuel des élèves (D8) | `presences_eleves` générées depuis les rapports qui donnent `absents_ids` (jamais depuis un simple nombre) ; `GET/PUT /admin/presences-eleves` | onglet Assiduité | inchangé (le rapport envoie déjà `absents_ids`) |
| Vues de pilotage (D8) | `/admin/tableau-de-bord/{vue-ensemble, soumissions, relances, assiduite, supervision, appui}` ; CSV pour relances et assiduité (journalisés) | Pilotage (D8) | — |
| Visites pédagogiques (D9.3) | grilles versionnées (brouillon → active → retirée), visites idempotentes (`client_op_id`), scores recalculés côté serveur | Visites, Grilles de visite | onglet Visites (hors ligne, file `SUBMIT_VISITE`) |
| Circuit d'appui (D9.3) | `demandes_appui` + historique : ouverte → affectée → en cours → résolue / abandonnée ; échéance, retard, responsable ; depuis visite, difficulté, besoin ou à la main | Demandes d'appui ; bouton « → Appui » dans Rapports journaliers | onglet Visites → Appui |
| Actualisation par API, rapports périodiques (D9.4) | clés de service (`X-Api-Key`, portées `analytique` / `tableau_bord`, empreinte seule conservée) ; `/service/analytique/*`, `/service/tableau-de-bord/{vue}` pseudonymisés ; `scripts/rapport_periodique.py` + timer hebdomadaire | Clés de service | — |
| Reprise de l'historique (D10.2) | lots, lignes (importée / quarantaine / doublon / rejetée), correspondances validées, réconciliation par total, IEF, école, acteur, semaine ; `scripts/reprise_historique.py` | Reprise de l'historique | — |
| Protocole minuteur (APP-33) | `sessions.protocole_minuteur` ; accès effectif calculé par le serveur (groupe → accès), exceptions avec motif obligatoire, historique immuable `assignations_recherche` | Protocole de recherche (matrice, alignement, historique) | minuteur masqué si `minuteur_visible = false` |
| Protection locale (APP-14) | — | — | SQLite chiffré SQLCipher (clé dans le Keystore), migration de l'ancienne base, verrou après 5 min, `allowBackup: false` |

Règles communes : une valeur absente n'est jamais affichée comme 0 (« non
calculable » avec la raison) ; « non applicable » et « non renseigné » ne
valent jamais zéro dans les scores ; chaque paramétrage, export et
changement d'accès est inscrit dans l'Historique.

## 2. Valeurs par défaut à valider par ARED

Elles sont actives tant qu'ARED ne les a pas remplacées ; le dashboard affiche
leur provenance (« proposé », « global », « campagne »).

| Paramètre | Valeur posée | Où la changer |
|---|---|---|
| Taux d'absence élève déclenchant une alerte | 20 % | Paramètres de campagne → Seuils |
| Absences consécutives | 3 jours attendus | idem |
| Retard de transmission toléré | 2 jours | idem |
| Délai maximal entre deux visites d'un tuteur | 30 jours | idem |
| Taux minimal par domaine de la grille | 50 % | idem |
| Grille de visite | brouillon « Grille de visite pédagogique » v1 : O1, P1, C1, R1, T1, LL1, LM1, J1 (échelle 0-3) + OUT1-3 (outils) — **non publiée** | Grilles de visite : compléter, puis Publier |
| Obligations | aucune : les vues « soumissions » et « relances » restent « non calculables » tant qu'elles ne sont pas saisies | Paramètres de campagne → Obligations |
| Calendrier | aucun jour attendu : à générer (jours de classe + exceptions) | Paramètres de campagne → Calendrier |
| Protocole minuteur | inactif sur toutes les campagnes | Paramètres de campagne → Protocole |
| Mapping de reprise | colonnes des exports Baseglobale / ElevesAbsents / difficulte / BaseBesoins (`reprise_service.MAPPING_DEFAUT`) | `--mapping fichier.json` du script |
| Conservation des audios d'enfants (APP-07) | **pas de purge** | `RETENTION_AUDIOS_JOURS` (`.env`) puis `scripts.purge_retention --appliquer` |
| Conservation de l'historique des modifications (APP-13) | **pas de purge** | `RETENTION_AUDIT_JOURS` puis `scripts.purge_retention --appliquer` |
| Conservation sur le téléphone des saisies déjà envoyées (APP-14) | **pas de purge** | `RETENTION_SAISIES_TELEPHONE_JOURS` : l'app purge après chaque synchronisation |

## 3. Mise en production

Pré-requis : une sauvegarde vérifiée (`scripts/backup.sh`, puis
`scripts/test_restauration.sh`) prise juste avant.

```bash
# Sur le serveur, dossier de l'API
git fetch origin && git checkout lot-L6-modules && git pull
./scripts/backup.sh
./redeploy_backend.sh            # le conteneur applique « alembic upgrade head » au démarrage
docker compose exec -T backend alembic current   # doit afficher w1x2y3z4a5b6 (head)
```

Rapport périodique hebdomadaire (optionnel, lundi 06:30) :

```bash
sudo cp deploy/systemd/ndawwune-rapport.service deploy/systemd/ndawwune-rapport.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ndawwune-rapport.timer
```

Dashboard : rebuild habituel de l'image admin sur `lot-L6-modules` (aucune
nouvelle variable). Mobile : build EAS `preview` puis `production` en
**1.2.0** ; après recette (`docs/RECETTE_TERRAIN_L2.md` §2), relever
`MIN_APP_VERSION=1.2.0` quand tous les téléphones sont à jour.

Retour arrière de la migration (après sauvegarde) :
`docker compose exec -T backend alembic downgrade v0w1x2y3z4a5`. Les rapports
importés par la reprise sont supprimés par le downgrade (ils n'existaient pas
avant).

## 4. Rattachement des données antérieures (APP-22)

Les rapports journaliers et pointages reçus avant L3 n'ont pas de campagne.
Le script ne rattache que les cas sans ambiguïté (une seule campagne affectée
au tuteur couvre la date) et liste les autres :

```bash
docker compose exec -T backend python -m scripts.rattacher_campagnes            # aperçu
docker compose exec -T backend python -m scripts.rattacher_campagnes --appliquer --detail /app/uploads/cas_rattachement.csv
```

## 5. Reprise de l'historique — déroulé

À faire d'abord sur une **copie isolée** de la base (recette), puis en production.

```bash
# 1. Figer une copie des sources exportées de Kobo (empreintes SHA-256, vérifiées à chaque étape)
docker compose exec -T backend python -m scripts.reprise_historique figer \
    --sources /app/uploads/reprise/sources-2024-25 --copie /app/uploads/reprise/2024-25
# 2. Analyser : en-têtes reconnus, doublons, codes à faire correspondre (CSV avec suggestions)
docker compose exec -T backend python -m scripts.reprise_historique analyser \
    --copie /app/uploads/reprise/2024-25 --sortie /app/uploads/reprise/correspondances_a_valider.csv
# 3. ARED complète la colonne cible_id, puis import des correspondances (dashboard → Reprise, ou :)
docker compose exec -T backend python -m scripts.reprise_historique correspondances \
    --fichier /app/uploads/reprise/correspondances_validees.csv --par "<nom du validateur>"
# 4. Simulation (aucune écriture), puis import réel dans un lot
docker compose exec -T backend python -m scripts.reprise_historique importer --copie /app/uploads/reprise/2024-25 \
    --nom "Campagne 2024-25" --campagne <uuid> --par "<nom>" --simulation
docker compose exec -T backend python -m scripts.reprise_historique importer --copie /app/uploads/reprise/2024-25 \
    --nom "Campagne 2024-25" --campagne <uuid> --par "<nom>"
```

Rien n'est rattaché par ressemblance de nom : une ligne sans correspondance
validée reste en quarantaine et se réimporte après validation. La page
« Reprise » montre la réconciliation ; le lot n'est accepté que si tous les
écarts sont expliqués.

## 6. Actions hors code (scripts fournis)

| Fiche | Script | Qui |
|---|---|---|
| APP-04 Rotation de `SECRET_KEY` | `scripts/exploitation/rotation_secret_key.sh` (contrôle seul par défaut, `--appliquer`) | exploitant du serveur, créneau hors séances |
| APP-05 Purge de l'historique git | `scripts/exploitation/purge_historique_git.sh <url>` (analyse seule par défaut, `--publier`) | propriétaire de chaque dépôt GitHub |
| Protection des branches | `scripts/exploitation/protection_branches.sh` (aperçu par défaut, `--appliquer`) | administrateur GitHub |
| APP-48 Sauvegardes | `scripts/backup.sh`, `scripts/test_restauration.sh`, timers `deploy/systemd` (voir `DEPLOIEMENT_ARED.md` §6) | responsable des sauvegardes |
