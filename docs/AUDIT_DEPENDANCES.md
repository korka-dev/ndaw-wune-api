# Inventaire des dépendances vulnérables (rapport V2, APP-12)

Audit rejoué le **20/11/2026** (`pip-audit`, `npm audit`) sur les branches
`lot-L1-permissions`. À rejouer à chaque lot et au moins une fois par mois. Les
CI de l'admin et du mobile publient le rapport `npm audit` à chaque exécution.

## API (`requirements.txt`)

| Paquet | Avant | Après | Motif |
|---|---|---|---|
| fastapi | 0.115.5 | 0.165.0 | requis pour starlette corrigé |
| starlette (transitif) | 0.41.3 | 1.10.0 | 7 avis (dont DoS multipart) |
| python-multipart | 0.0.12 | 0.0.31 | 7 avis (DoS sur l'analyse des formulaires) |
| python-jose | 3.3.0 | 3.5.0 | 4 avis corrigés |
| pdfplumber → pdfminer.six | 0.11.4 → 20231228 | 0.11.10 → 20260107 | 2 avis |
| prometheus-fastapi-instrumentator | 7.0.2 | 8.1.0 | compatibilité starlette 1.x |
| slowapi | 0.1.9 | 0.1.10 | maintenance |
| pytest / pytest-asyncio | 8.3.4 / 0.24.0 | 9.0.3 / 1.5.0 | 1 avis (outillage de test) |

Vérification : 101 tests passés (dont intégration PostgreSQL + Redis),
`alembic check` sans écart.

**Résiduel (sans correctif publié) :**

- `python-jose 3.5.0` (PYSEC-2026-4298) et `ecdsa 0.19.2` (PYSEC-2026-1325,
  dépendance de python-jose). L'API signe en **HS256** avec le moteur
  `cryptography` : le code ECDSA n'est pas exercé. Action recommandée au lot
  L5 : remplacer python-jose par PyJWT (changement limité à
  `app/core/security.py` et `app/core/rate_limit.py`).

## Admin (`package.json`)

| Paquet | Avant | Après | Motif |
|---|---|---|---|
| next / eslint-config-next | 15.5.22 | 15.5.29 | 1 critique (exécution de code à distance) |
| axios et transitifs | — | correctifs mineurs (`npm audit fix`) | prototype pollution, follow-redirects |
| zod | 3.23.8 | **retiré** | inutilisé dans le code ; correctif seulement en v4 |

Résultat : **0 vulnérabilité**. `tsc` et `next build` passent.

## Application mobile (`package.json`)

`npm audit fix` sans montée majeure : **42 → 22** avis, **0 critique**
(handlebars, shell-quote, axios, node-forge, ws, undici, form-data corrigés).

**Résiduel :** 5 élevés, 17 modérés, tous dans la chaîne Expo SDK 54
(`expo-router` → `query-string` → `decode-uri-component`, `@expo/cli`,
`@expo/config`, `image-size`, `glob`, `minimatch`). Le correctif proposé est une
montée de SDK Expo (majeure). Ces paquets servent surtout au **build** et au
serveur de développement. Seul `decode-uri-component` est présent dans
l'application installée : il traite les liens profonds, et ceux-ci ne viennent
pas d'une source extérieure.

Action recommandée : montée de SDK Expo planifiée hors période de terrain,
avec une recette complète hors ligne. Point déjà existant, sans lien avec ce lot :
`npx expo install --check` signale `expo-local-authentication` 17.0.8, alors que
17.0.9 est attendu.
