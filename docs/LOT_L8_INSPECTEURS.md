# Lot L8 — Inspecteurs (IEF) en consultation

## Ce que fait le lot

- **Admin** (« Gestion Inspecteurs ») : création d'un compte inspecteur (nom,
  téléphone obligatoire, email et fonction facultatifs, IEF choisie parmi les
  libellés « Région (IEF) » des écoles). Mot de passe temporaire, changement
  forcé à la première connexion. Réinitialisation depuis « Identifiants ».
- **Application mobile** (1.4.0) : l'inspecteur se connecte avec son
  téléphone et consulte, **en lecture seule**, les écoles de son IEF, leurs
  tuteurs et superviseurs, et les rapports journaliers (photos comprises). Il
  dispose d'une aide propre et peut signaler un problème. Les dernières
  données consultées restent lisibles hors ligne (date affichée).
- **Périmètre** : écoles dont `schools.region` = IEF du compte (casse et
  espaces ignorés), plus les rapports anciens sans école rattachée dont l'IEF
  saisie correspond. Hors périmètre : 404. Aucune écriture possible.

## Données personnelles

`INSPECTEUR_NOMS_ABSENTS=false` (défaut) : l'inspecteur voit le **nombre**
d'absents, pas leurs noms. Passer à `true` seulement sur décision écrite
d'ARED. Les champs libres (commentaires, description des difficultés) restent
visibles tels que saisis.

## Préalable de données

Chaque école doit porter son IEF dans « Région (IEF) », avec un libellé
identique pour toutes les écoles d'une même IEF. Une école sans IEF
n'apparaît chez aucun inspecteur. Contrôle avant ouverture :

```sql
SELECT trim(region) AS ief, count(*) FROM schools
WHERE archived_at IS NULL GROUP BY 1 ORDER BY 1;
```

## Mise en production

Le lot part de `main` (L6 et L7 inclus) : si la production est encore en L5,
les migrations L6, L7 et L8 s'appliquent ensemble.

1. Sauvegarde :
   ```bash
   cd /opt/ndawwune/api && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner' > ~/backups/ndawwune_prod_avant_L8.dump && ls -lh ~/backups
   ```
2. `.env` de l'API : ajouter `INSPECTEUR_NOMS_ABSENTS=false`, renseigner
   `SUPPORT_CONTACT` (affiché dans l'aide de l'inspecteur), et, une fois
   l'APK distribué, `LATEST_APP_VERSION=1.4.0`.
3. API :
   ```bash
   git fetch origin && git checkout lot-L8-inspecteurs && git pull && API_DOMAIN=api.ndawwune.ared-edu.org bash scripts/redeploy.sh
   ```
   (`main` une fois la branche fusionnée.) Migration ajoutée :
   `y3z4a5b6c7d8` (valeur `inspecteur` du type `user_role`, colonne `users.ief`).
4. Admin :
   ```bash
   cd /opt/ndawwune/admin && git fetch origin && git checkout lot-L8-inspecteurs && git pull && bash deploy_docker.sh
   ```
5. Mobile : construire l'APK 1.4.0 (`versionCode` 140) et le distribuer. Les
   versions antérieures continuent de fonctionner pour les tuteurs et
   superviseurs ; un inspecteur a besoin de la 1.4.0.
6. Vérification : `scripts/verifier_deploiement.sh`, création d'un compte
   inspecteur de test sur une IEF, connexion mobile, puis archivage du compte.

## Retour arrière

Revenir au commit précédent et relancer `scripts/redeploy.sh`. La migration se
défait avec `alembic downgrade x2y3z4a5b6c7`, **après sauvegarde** ; elle est
refusée tant qu'un compte inspecteur existe (l'archiver ou changer son rôle).
