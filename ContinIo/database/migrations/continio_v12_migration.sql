USE continio;

-- Existing ContinIo databases normally already contain these fields.
-- Run DESCRIBE students first. Execute only the missing ALTER statement(s).
-- ALTER TABLE students ADD COLUMN face_embedding JSON NULL;
-- ALTER TABLE students ADD COLUMN photo_path VARCHAR(500) NULL;

-- Privacy cleanup: v12 never uses photo_path.
UPDATE students SET photo_path = NULL WHERE photo_path IS NOT NULL;
