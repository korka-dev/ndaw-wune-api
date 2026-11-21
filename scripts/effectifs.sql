-- Effectifs de référence (APP-48) : écrits à la sauvegarde, recomparés après restauration.
SELECT 'users', count(*) FROM users UNION ALL
SELECT 'schools', count(*) FROM schools UNION ALL
SELECT 'eleves', count(*) FROM eleves UNION ALL
SELECT 'program_sessions', count(*) FROM program_sessions UNION ALL
SELECT 'planning_segments', count(*) FROM planning_segments UNION ALL
SELECT 'seances', count(*) FROM seances UNION ALL
SELECT 'rapports_journalier', count(*) FROM rapports_journalier UNION ALL
SELECT 'evaluations_eleves', count(*) FROM evaluations_eleves UNION ALL
SELECT 'evaluation_tirages', count(*) FROM evaluation_tirages UNION ALL
SELECT 'audit_logs', count(*) FROM audit_logs
ORDER BY 1;
