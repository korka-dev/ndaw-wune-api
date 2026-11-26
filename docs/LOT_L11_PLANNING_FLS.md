# Lot L11 — Un emploi du temps par type d'école (avec / sans FLS)

## Ce qui change

- Chaque créneau appartient à un planning : **FLS**, **sans FLS** ou **toutes les écoles**
  (`planning_segments.type_ecole`, migration `a5b6c7d8e9f0`). Les créneaux existants
  restent « toutes les écoles », sauf ceux réservés FLS (lot L9) qui passent en « FLS ».
- **Gestion Planning** : boutons « Importer planning FLS » et « Importer planning sans FLS ».
  Les emplois du temps Word (.docx) sont lus directement :
  - `NWAS 2026-2027_Emploi du temps.docx` → planning sans FLS (19 créneaux : lundi,
    mercredi, vendredi) ;
  - `NWAS_2026-27_Emploi du temps_FLS.docx` → planning FLS (21 créneaux).
  Importer un planning remplace ce planning et les créneaux « toutes les écoles » de la
  campagne, jamais l'autre planning. Badge « Planning FLS / sans FLS » sur chaque créneau ;
  choix du planning dans le formulaire d'un créneau.
- **App mobile (minuteur, accueil, prochains créneaux)** : le serveur n'envoie au tuteur
  que le planning de son école — école avec FLS → planning FLS ; école sans FLS ou non
  renseignée → planning sans FLS. Aucun nouvel APK nécessaire.
- Nouvelle dépendance : `python-docx`.

## Mise en production

1. Sauvegarde de la base, puis déployer l'API (`redeploy.sh` applique la migration)
   et l'admin sur la branche `lot-L11-planning-fls`.
2. Gestion Planning → « Importer planning FLS » → fichier FLS → vérifier l'aperçu → confirmer.
3. **Aussitôt après**, « Importer planning sans FLS » → l'autre fichier → confirmer.
   (Entre les deux étapes, les écoles sans FLS n'ont plus de créneau : le premier import
   retire les anciens créneaux communs.)
4. Vérifier le résumé : « Créneaux : 21 planning FLS · 19 planning sans FLS ».
