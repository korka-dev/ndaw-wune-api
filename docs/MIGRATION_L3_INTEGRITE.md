# Mise en production du lot L3 (intégrité et administration)

Migration Alembic : `t8u9v0w1x2y3_integrite_l3` (réversible).

## Avant la migration

1. **Sauvegarde** de la base (voir `SECURITE_EXPLOITATION.md`, APP-48).
2. **Collisions d'e-mails** (APP-35) : la migration crée un index unique sur
   `lower(email)` et **refuse de s'appliquer** si deux comptes ne diffèrent que
   par la casse. Lister les cas :

   ```bash
   docker compose exec api python -m scripts.collisions_identifiants
   ```

   Chaque collision est à arbitrer par ARED (quel compte garder, à qui
   rattacher l'historique). Rien n'est fusionné automatiquement.
3. **Écoles en double** (APP-23) : l'appariement ne fusionne plus deux écoles
   sur le seul nom. Les doublons existants se listent avec :

   ```bash
   docker compose exec api python -m scripts.doublons_ecoles
   ```

   La fusion éventuelle reste une décision ARED (rapport pour arbitrage).

## Ce qui change pour l'administration

- « Supprimer » **archive** écoles, classes, élèves, comptes et campagnes :
  l'historique est conservé, restauration possible (« Afficher les archivés »).
  La suppression définitive (`?definitif=true`) est refusée dès qu'un
  historique existe (409 `HISTORIQUE_PRESENT`) ; en base, les clés étrangères
  des observations sont en `RESTRICT`.
- Un rapport journalier n'est plus supprimable (410) : annulation motivée
  (statut `annule`, exclu des indicateurs et exports par défaut) ou correction
  versionnée. L'historique des versions est consultable dans l'admin.
- Les imports (global et planning) présentent un **aperçu** sans rien
  enregistrer avant confirmation. Import global : partiel ligne par ligne,
  chaque ligne refusée est annulée seule. Planning : remplacement refusé
  (409 `PLANNING_IMPORT_PARTIEL`) s'il reste des lignes invalides non confirmées.
- Uploads : 413 au-delà de la taille autorisée (en-tête `Content-Length`),
  classeurs Excel bornés (lignes, colonnes, décompression).

## Paramètres à arbitrer (ARED)

- `DELAI_ENVOI_TARDIF_JOURS` (14 jours, `app/services/seance_service.py`) :
  délai après la fin d'une campagne pendant lequel une séance hors ligne est
  encore acceptée.
- Les séances, rapports et pointages reçus sont rattachés à la campagne active
  à la réception (`session_id`) ; les données antérieures restent sans campagne.
