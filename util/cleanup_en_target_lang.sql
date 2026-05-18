-- ─────────────────────────────────────────────────────────────────────────────
-- Fix mislabeled 'en' / 'EN-US' target_lang data for English songs.
--
-- Root cause: generate_song_data.py was called without --target-lang, so it
-- defaulted to 'EN-US'. The backend normalized this to 'en' when storing.
--
-- word_definitions: rows with target_lang='en' hold Russian kaikki text (correct
--   content, wrong key). DELETE them — fill_word_translations will re-add as 'ru'.
--
-- line_translations: rows with target_lang='en' hold the original English text
--   (en→en identity "translation"). DELETE them — fill_line_translations will
--   add proper Russian translations.
--
-- songs.target_langs: remove 'en' / 'EN-US' from English songs that had it
--   added by the backend when it parsed the embedded translations dict.
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. Delete mislabeled word_definitions
DELETE FROM word_definitions wd
USING words w, lines l, songs s
WHERE wd.word_id    = w.id
  AND w.line_id     = l.id
  AND l.song_id     = s.id
  AND s.language_code = 'en'
  AND wd.target_lang IN ('en', 'EN-US');

-- 2. Delete useless line_translations (en→en identity)
DELETE FROM line_translations lt
USING lines l, songs s
WHERE lt.line_id  = l.id
  AND l.song_id   = s.id
  AND s.language_code = 'en'
  AND lt.target_lang IN ('en', 'EN-US');

-- 3. Remove 'en' / 'EN-US' from songs.target_langs for English songs
--    target_langs is stored as a JSON text array, e.g. '["en","ru"]'
UPDATE songs
SET target_langs = COALESCE(
    (SELECT json_agg(elem)::text
       FROM jsonb_array_elements_text(target_langs::jsonb) AS elem
      WHERE elem NOT IN ('en', 'EN-US')),
    '[]'
)
WHERE language_code = 'en'
  AND (target_langs::jsonb @> '["en"]'::jsonb
    OR target_langs::jsonb @> '["EN-US"]'::jsonb);

COMMIT;
