# Déploiement sur le serveur ARED — lot L5 (rapport V2)

Ce guide s'adresse à la personne qui installe et exploite Ndaw Wune sur le serveur ARED. Il couvre APP-42 à APP-48 : domaines, variables, bascule, retour arrière, sauvegardes, supervision, support et recette finale. Il ne contient aucun secret.

## 0. État constaté avant bascule (à trancher)

| Composant | URL d'API configurée | Où |
|---|---|---|
| Mobile (preview + production) | `https://api.ndawwune.ared-edu.org/api/v1` | `.env.production`, `eas.json` (commit `1b4790a`, 10/11/2026) |
| Dashboard admin | `https://api.ndawwune.cloud/api/v1` | `.env.production` du serveur admin |
| API (Caddy, CORS par défaut) | `api.ndawwune.cloud` / `admin.ndawwune.cloud` | `deploy_vps.sh`, `Caddyfile.api` |

**Décision ARED attendue :** confirmer les domaines définitifs (API et dashboard) et la date de bascule. Tant que le mobile et le dashboard ne visent pas le même serveur, leurs données divergent. Les contrôles ci-dessous le détectent.

## 1. Variables : build ou démarrage

Une variable « build » est **inscrite dans le code livré**. La changer sur le serveur ne déplace pas les clients déjà construits.

| Composant | Variable | Type | Effet |
|---|---|---|---|
| API | `API_DOMAIN`, `ADMIN_DOMAIN` | installation (`deploy_vps.sh`) | site Caddy, `CORS_ORIGINS` |
| API | `API_DOMAINS_TRANSITION` | installation | anciens domaines encore servis (APK non mis à jour) |
| API | `CORS_ORIGINS` | démarrage (`.env`) | origines autorisées, `https://` seulement en production ; avertissement au démarrage si vide |
| API | `DATABASE_URL`, `REDIS_URL`, `REDIS_PASSWORD`, `SECRET_KEY`, `UPLOADS_DIR` | démarrage | voir `SECURITE_EXPLOITATION.md` |
| API | `MIN_APP_VERSION`, `LATEST_APP_VERSION` | démarrage | versions mobiles acceptées (426 en dessous) |
| API | `APP_COMMIT` | build image | version affichée par `/health/ready` |
| Admin | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_APP_ORIGIN`, `NEXT_PUBLIC_APP_VERSION` | **build** | le build échoue si le bundle vise une autre API (`scripts/verifier-bundle.sh`) |
| Admin | `API_URL` | démarrage | appels serveur (connexion, renouvellement) ; repli sur `NEXT_PUBLIC_API_URL` |
| Mobile | `EXPO_PUBLIC_API_URL` | **build / bundle OTA** | `.env.production` et `eas.json`, à garder identiques (`npm run verifier:bundle`) |
| Mobile | `expo.updates.url`, `runtimeVersion` | **build APK** | canal OTA ; non modifiable sans réinstallation |

## 2. DNS, certificats, volumes, comptes

1. **DNS.** Créer les enregistrements A/AAAA de l'API et du dashboard vers le serveur ARED, **avant** d'installer Caddy, faute de quoi Let's Encrypt échoue. Un TTL court (300 s) quelques jours avant la bascule permet de revenir en arrière rapidement.
2. **Certificats.** Ils sont émis et renouvelés automatiquement par Caddy. Les ports 80 et 443 doivent être ouverts.
3. **Volumes Docker.**
   - `postgres_data` : la base, nominative.
   - `uploads_data` : photos, audios et ressources, nominatif.
   - Ces volumes ne doivent jamais être supprimés sans une sauvegarde vérifiée (§6).
4. **Comptes techniques.** Pour chacun, nommer un propriétaire côté ARED et un suppléant :

| Compte | Usage |
|---|---|
| Serveur (SSH) | Installation, exploitation |
| Registrar / DNS | Domaines |
| Expo / EAS (`korka-diallo`) | Builds, OTA |
| Google Play | Publication de l'APK |
| Stockage de sauvegarde externe | Copie hors serveur |
| Webhook d'alerte | Canal de recette puis d'exploitation |
| GitHub | Code, CI |

## 3. Installation sur le serveur ARED

```bash
# API
API_DOMAIN=api.<domaine-ared> ADMIN_DOMAIN=admin.<domaine-ared> sudo -E bash deploy_vps.sh
```

```bash
# Dashboard (NEXT_PUBLIC_API_URL=https://api.<domaine-ared>/api/v1 dans .env.production)
API_DOMAIN=api.<domaine-ared> ADMIN_DOMAIN=admin.<domaine-ared> bash deploy_docker.sh
```

Puis contrôler depuis n'importe quel poste :

```bash
API_URL=https://api.<domaine-ared> ADMIN_ORIGIN=https://admin.<domaine-ared> ./scripts/verifier_deploiement.sh
```

Ce script vérifie :
- DNS et HTTPS ;
- `/health` et `/health/ready` (base et migrations, Redis, médias) ;
- CORS : le dashboard est autorisé, une origine étrangère est refusée ;
- la politique de version mobile ;
- que `/metrics` n'est pas public.

Le code de sortie est non nul en cas d'écart.

## 4. Plan de bascule

1. **J-7.**
   - Restaurer la dernière sauvegarde de l'ancien serveur sur le serveur ARED avec `scripts/restore.sh`.
   - Lancer `scripts/test_restauration.sh` et consigner la preuve.
   - Faire la recette (§8) sur le serveur ARED avec des comptes de test.
2. **J-2.** Réduire le TTL DNS et annoncer la fenêtre d'indisponibilité aux superviseurs. Les tuteurs peuvent continuer hors ligne : la file d'attente conserve les saisies.
3. **J0.**
   1. Couper l'écriture sur l'ancien serveur : arrêter le backend, Caddy répond 502 et les mobiles mettent leurs envois en file.
   2. Faire la sauvegarde finale avec `scripts/backup.sh`.
   3. La restaurer sur ARED avec `CONFIRM=oui scripts/restore.sh`, puis vérifier les effectifs.
   4. Basculer le DNS, ou installer `API_DOMAINS_TRANSITION=<ancien domaine>` côté ARED si l'ancien domaine est conservé.
   5. Lancer `verifier_deploiement.sh` sur chaque domaine.
   6. Rebuilder le dashboard avec la nouvelle `NEXT_PUBLIC_API_URL`.
   7. Publier l'OTA mobile si l'URL d'API change, avec `npm run verifier:bundle -- --export` puis `eas update`.
4. **Transition.** Les APK déjà installés gardent l'URL d'API inscrite dans leur bundle jusqu'à la réception de l'OTA (au lancement suivant, avec du réseau). Pendant la transition :
   - l'ancien domaine doit pointer vers le serveur ARED, ou être servi par lui via `API_DOMAINS_TRANSITION` ;
   - suivre dans les journaux les requêtes reçues sur l'ancien domaine ;
   - une fois tous les terminaux mis à jour, relever `MIN_APP_VERSION` : les retardataires reçoivent l'écran de mise à jour et leur file est conservée.

   Un APK sans `expo.updates` (antérieur au 29/07/2026) doit être réinstallé : voir `BUILD.md`.
5. **Fin de transition.** Retirer l'ancien domaine et archiver l'ancien serveur, **après** au moins une sauvegarde ARED restaurée avec succès.

## 5. Retour arrière

| Composant | Retour arrière |
|---|---|
| DNS | Rétablir l'ancien enregistrement (TTL court) |
| API | Rebuilder avec le commit précédent : `git checkout <commit>`, puis `scripts/redeploy.sh`. `/health/ready` affiche `version` (commit). La migration L4 se défait avec `alembic downgrade t8u9v0w1x2y3`, **après sauvegarde**. |
| Données écrites sur ARED depuis la bascule | Faire une sauvegarde ARED avant le retour arrière, puis la restaurer sur l'ancien serveur. Ne jamais faire tourner deux serveurs en écriture en même temps. |
| Mobile | `eas update:roll-back-to-embedded --branch <canal>` ou republier l'OTA précédent |
| Dashboard | Rebuilder l'image avec l'ancienne `NEXT_PUBLIC_API_URL` |

## 6. Sauvegardes et restauration (APP-48)

**Installation des tâches planifiées (systemd)** :

```bash
sudo mkdir -p /etc/ndawwune
sudo install -m 600 deploy/systemd/exploitation.env.example /etc/ndawwune/exploitation.env
sudoedit /etc/ndawwune/exploitation.env   # BACKUP_REMOTE, ALERTE_WEBHOOK_URL…
sudo cp deploy/systemd/ndawwune-*.service deploy/systemd/ndawwune-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ndawwune-backup.timer ndawwune-restauration.timer ndawwune-sante.timer
```

**Ce que font les tâches :**

| Tâche | Fréquence | Effet |
|---|---|---|
| `ndawwune-backup` | chaque nuit (02:00) | `pg_dump` + médias + empreintes + `EFFECTIFS.txt`, puis copie vers `BACKUP_REMOTE` (rsync ou rclone). En cas d'échec, une alerte part sur le webhook. |
| `ndawwune-restauration` | le 1er du mois | `scripts/test_restauration.sh` restaure la dernière sauvegarde dans un PostgreSQL **jetable**, sans toucher la production, puis compare effectifs, nombre de fichiers et fichiers référencés en base. La preuve datée et chronométrée est écrite dans `RESTAURATION_<date>.txt`. |

**À fixer par ARED :**
- un responsable des sauvegardes ;
- la perte de données admissible (RPO ; sauvegarde quotidienne, soit 24 h au plus aujourd'hui) ;
- le délai de restauration (RTO ; à mesurer avec la preuve) ;
- la rétention locale (`RETENTION_DAYS`) et celle de la copie externe ;
- l'emplacement de la copie externe, chiffrée et à accès restreint.

**Aucune migration irréversible ni suppression avant une première preuve conforme.**

## 7. Supervision et support (APP-45)

- **Disponibilité** : `/health` (ou `/health/live`) indique que le processus répond. **Préparation** : `/health/ready` contrôle chaque dépendance avec un délai borné (3 s) et renvoie 503 dès qu'une d'elles est en échec :
  - base et migrations à jour ;
  - Redis ;
  - écriture des médias.

  Le healthcheck Docker utilise `/health/ready` : le conteneur passe « unhealthy » sans redémarrer en boucle.
- **Alertes** :
  - `ndawwune-sante.timer` interroge `/health/ready` toutes les 2 minutes et poste sur `ALERTE_WEBHOOK_URL` à **chaque changement d'état** (dégradé, puis rétabli), avec les contrôles en échec et leur code ;
  - pour tester le canal en recette, lancer `scripts/surveiller_sante.sh --test` ;
  - la pile `monitoring/` (Prometheus, Alertmanager) complète ce dispositif : service tombé, erreurs 5xx, latence, disque.
- **Références support** :
  - chaque requête porte un `X-Request-ID` (`adm-…` ou `mob-…`), repris dans les journaux de l'API et renvoyé dans les réponses ; une erreur 500 inclut `reference` ;
  - le dashboard signale ses pannes (5xx, serveur injoignable, plantage d'écran) à `/client-errors` et affiche une référence `SUP-…` copiable ;
  - le mobile signale ses 5xx, au plus une fois par minute et par route ; le centre de synchronisation affiche `réf. mob-…` sur l'erreur concernée.

  Pour retrouver un incident : `docker compose logs backend | grep -E 'SUP-20261121-ABC123|mob-…'`.

  Les signalements ne contiennent que des champs techniques. Côté API, les e-mails, numéros, jetons et données binaires éventuels sont masqués avant écriture dans les journaux.
- **Attribution** : ARED désigne qui reçoit les alertes et qui traite les références support. Les alertes de test vont uniquement sur le canal de recette convenu.

## 8. Recette finale (critères du rapport)

| Fiche | Preuve attendue | Comment |
|---|---|---|
| APP-42 | Tous les clients de recette pointent vers ARED ; CORS limité aux origines retenues | `verifier_deploiement.sh`, `verifier-bundle.sh` (admin), `npm run verifier:bundle -- --export` (mobile) |
| APP-43 | Démarrage avec mot de passe Redis et port publié personnalisé ; Redis coupé → comportement prévu | `REDIS_PASSWORD=… REDIS_PORT=6380 docker compose up -d` puis `docker compose stop redis` : `/health/ready` passe à 503 (`redis=INDISPONIBLE`), et le renouvellement de session répond 503 sans déconnecter |
| APP-44 | Une régression d'accès ou de rejeu bloque la livraison | CI (`.github/workflows/ci.yml`) + protection de branche GitHub (à activer par le propriétaire du dépôt) |
| APP-45 | Base, Redis et médias coupés séparément → incident visible et attribuable ; alerte de test reçue sur le canal convenu | `docker compose stop db` puis `redis` ; `chmod a-w` sur le volume des médias (recette) ; `surveiller_sante.sh --test` ; erreur client fictive → `SUP-…` retrouvé dans les journaux |
| APP-46 | Matrice ancien/nouveau client × API ; file d'attente préservée | APK N-1 avec saisies en file, puis API L5, puis `MIN_APP_VERSION` relevé : écran de mise à jour, file conservée et envoyée après mise à jour |
| APP-48 | Restauration datée et chronométrée, effectifs et fichiers cohérents, copie hors serveur | `RESTAURATION_<date>.txt` conforme ; présence de la copie sur `BACKUP_REMOTE` |
| Terrain | Validation par tuteurs, superviseurs et admin sur ARED | Parcours de recette L1 sur le serveur ARED |

## 9. Lot L6 (modules de la Partie II)

Détail et valeurs par défaut : `docs/LOT_L6_MODULES.md`. En bref :

1. Sauvegarde vérifiée (`scripts/backup.sh`), puis `./redeploy_backend.sh` sur
   `lot-L6-modules` : la migration `w1x2y3z4a5b6` s'applique au démarrage
   (`docker compose exec -T backend alembic current` pour vérifier).
2. Rebuild du dashboard sur `lot-L6-modules` (aucune nouvelle variable).
3. Timer optionnel du rapport périodique : `ndawwune-rapport.timer` (§6, même
   procédure d'installation ; `RAPPORTS_DIR` et le webhook dans
   `/etc/ndawwune/exploitation.env`).
4. Mobile 1.2.0 : **nouvel APK** (SQLCipher, verrou), recette §2 de
   `RECETTE_TERRAIN_L2.md`, puis `MIN_APP_VERSION=1.2.0`.
5. Paramétrage par ARED dans le dashboard : calendrier, obligations, seuils,
   publication de la grille de visite, protocole minuteur.
6. Retour arrière : `alembic downgrade v0w1x2y3z4a5` (après sauvegarde).
