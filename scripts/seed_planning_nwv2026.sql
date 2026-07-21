-- ============================================================
-- Ndaw Wune Vacances 2026 – Emploi du temps
-- Script d'insertion du planning officiel (PDF 17/07/2026)
--
-- Même programme chaque jour (semaine = NULL → toutes les semaines)
-- Jour 1 → jour=0, Jour 2 → jour=1, ... Jour 7 → jour=6
-- ============================================================

BEGIN;

-- 1. Supprimer tout le planning existant pour cette session
DELETE FROM planning_segments
WHERE session_id = '5b759235-a384-4e4a-9de1-e2c015e2ca85';

-- 2. Insérer le planning NWV 2026 pour chaque Jour (0 à 6)
INSERT INTO planning_segments (id, session_id, semaine, jour, heure_debut, heure_fin, matiere, classe, created_at, updated_at)
VALUES
  -- ── Jour 1 (jour = 0) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 0, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 2 (jour = 1) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 1, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 3 (jour = 2) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 2, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 4 (jour = 3) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 3, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 5 (jour = 4) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 4, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 6 (jour = 5) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 5, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW()),

  -- ── Jour 7 (jour = 6) ─────────────────────────────────────────
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '09:00', '09:05', 'Promesse Ndaw Wune',                            NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '09:05', '09:10', 'Chanson de l''alphabet',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '09:10', '09:25', 'Cahier de récits',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '09:25', '09:55', 'Leçon de mathématiques',                        NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '09:55', '10:00', 'Chanson de transition',                         NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '10:00', '10:40', 'Leçon de lecture',                              NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '10:40', '11:00', 'Jeux éducatif',                                 NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '11:00', '11:20', 'Récréation',                                    NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '11:20', '11:50', 'Français langue seconde',                       NULL, NOW(), NOW()),
  (gen_random_uuid(), '5b759235-a384-4e4a-9de1-e2c015e2ca85', NULL, 6, '11:50', '12:10', 'Lecture indépendante avec feedback constructif', NULL, NOW(), NOW());

COMMIT;

-- Vérification
SELECT jour, COUNT(*) as nb_activites,
       MIN(heure_debut::text) as debut,
       MAX(heure_fin::text) as fin
FROM planning_segments
WHERE session_id = '5b759235-a384-4e4a-9de1-e2c015e2ca85'
GROUP BY jour ORDER BY jour;
