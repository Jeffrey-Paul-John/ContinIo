USE continio;

-- Recalculate only unsubmitted records without a teacher decision.
UPDATE attendance AS a
JOIN (
    SELECT
        session_id,
        student_id,
        COUNT(*) AS completed_waves,
        SUM(result = 'present') AS present_waves
    FROM wave_results
    GROUP BY session_id, student_id
) AS waves
    ON waves.session_id = a.session_id
   AND waves.student_id = a.student_id
SET
    a.automated_status = CASE
        WHEN waves.completed_waves < 3 THEN 'review'
        WHEN waves.present_waves = 3 THEN 'present'
        WHEN waves.present_waves = 2 THEN 'review'
        ELSE 'absent'
    END,
    a.final_status = CASE
        WHEN waves.completed_waves < 3 THEN NULL
        WHEN waves.present_waves = 3 THEN 'present'
        WHEN waves.present_waves = 2 THEN NULL
        ELSE 'absent'
    END
WHERE a.teacher_status IS NULL
  AND a.submitted_at IS NULL;
