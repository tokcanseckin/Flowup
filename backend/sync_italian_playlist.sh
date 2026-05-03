#!/usr/bin/env bash
# Ensures playlist 2 contains all Italian songs currently in songs table.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/backend/flowup.db"
API="http://127.0.0.1:8000"
PLAYLIST_ID=2

# Remove orphan links first.
sqlite3 "$DB" "DELETE FROM playlist_songs WHERE song_id NOT IN (SELECT id FROM songs);"

# Add every Italian song to playlist 2 (idempotent if backend rejects duplicates).
for id in $(sqlite3 "$DB" "SELECT id FROM songs WHERE language_code='it' ORDER BY id;"); do
  curl -s -X POST "$API/api/playlists/$PLAYLIST_ID/songs" \
    -H "Content-Type: application/json" \
    -d "{\"song_id\": $id}" >/dev/null || true
done

songs_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM songs WHERE language_code='it';")
playlist_count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM playlist_songs WHERE playlist_id=$PLAYLIST_ID AND song_id IN (SELECT id FROM songs WHERE language_code='it');")

echo "Italian songs in DB: $songs_count"
echo "Italian songs in playlist $PLAYLIST_ID: $playlist_count"
