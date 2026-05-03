#!/usr/bin/env bash
# Processes all 40 Italian songs and adds them to playlist 2.

set -euo pipefail

PYTHON="/Users/seckintokcan/Documents/The Foundry/Active/Flowup/.venv/bin/python"
API="http://127.0.0.1:8000"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAYLIST_ID=2
PY=("$PYTHON" "$SCRIPT_DIR/generate_song_data.py" --lang it --api-url "$API")

run() {
  local artist="$1" title="$2" uri="$3" display="$4"
  echo "=== Processing: $display ==="
  local song_id
  song_id=$("${PY[@]}" --artist "$artist" --title "$title" \
    --spotify-uri "$uri" --display-title "$display" 2>&1 | \
    tee /dev/stderr | grep -o 'Song stored (id=[0-9]*' | grep -o '[0-9]*$' || true)

  if [ -n "$song_id" ]; then
    curl -sf -X POST "$API/api/playlists/$PLAYLIST_ID/songs" \
      -H "Content-Type: application/json" \
      -d "{\"song_id\": $song_id}" >/dev/null && \
      echo "  → Added to playlist $PLAYLIST_ID (song_id=$song_id)"
  else
    echo "WARN: skipped (no song_id returned)"
  fi
  echo ""
}

run "Domenico Modugno" "Nel blu dipinto di blu" "spotify:track:5zyrEv4F3FaLECI8TOKpFM" "Nel blu dipinto di blu (Volare)"
run "Toto Cutugno" "L'Italiano" "spotify:track:2S7RApTsKT0CtYojYq2cKz" "L'Italiano"
run "Adriano Celentano" "Azzurro" "spotify:track:0rcDm9dwoLMzkGTul3Z01r" "Azzurro"
run "Andrea Bocelli" "Con te partirò" "spotify:track:0k3TzavMNjPyFNrjQPiFhP" "Con te partirò"
run "Lucio Dalla" "Caruso" "spotify:track:6HYm04mKq02OtLMf6sGEFR" "Caruso"
run "Lucio Battisti" "La canzone del sole" "spotify:track:5XAGVMePJNaXHcdRN578Xi" "La canzone del sole"
run "Lucio Battisti" "Il mio canto libero" "spotify:track:2SvXqxiG2ntfkEWvuABT7u" "Il mio canto libero"
run "Fabrizio De André" "Il pescatore" "spotify:track:3NAYfdkUg1D0ZFLOdXhM6i" "Il pescatore"
run "Rino Gaetano" "Ma il cielo è sempre più blu" "spotify:track:314QKsjuS2Ax3Un4gxUCbb" "Ma il cielo è sempre più blu"
run "Franco Battiato" "Centro di gravità permanente" "spotify:track:6OzeWNA1SQgZBw5caqAIKN" "Centro di gravità permanente"
run "Franco Battiato" "La cura" "spotify:track:2ljTahDnUsH3aQvH39hYkP" "La cura"
run "Mina" "Se telefonando" "spotify:track:2bYLru2w0NKtCDiFPgALFE" "Se telefonando"
run "Gino Paoli" "Il cielo in una stanza" "spotify:track:37C6DyoMu75ViTiwqxV4bY" "Il cielo in una stanza"
run "Pino Daniele" "Napule è" "spotify:track:7A7HVrOpqRyXSVpZ9rp8AD" "Napule è"
run "Francesco De Gregori" "La donna cannone" "spotify:track:2T2t1DXwzdilKF3BQPHREo" "La donna cannone"
run "Eros Ramazzotti" "Più bella cosa" "spotify:track:7dATdrUrfmCJXgfkaN4fcw" "Più bella cosa"
run "Eros Ramazzotti" "Se bastasse una canzone" "spotify:track:38vvydlwbFzyg3JfpYlv5o" "Se bastasse una canzone"
run "Laura Pausini" "La solitudine" "spotify:track:5bxQHscWvyaQbm37igKP4K" "La solitudine"
run "Laura Pausini" "Strani amori" "spotify:track:2HSRazkijnq7r6tgIykynY" "Strani amori"
run "Jovanotti" "A te" "spotify:track:0XtVfBWnnWDwZveSlsAyKx" "A te"
run "Jovanotti" "Ragazzo fortunato" "spotify:track:0HM26rcXCrjHgfrBfTveks" "Ragazzo fortunato"
run "Vasco Rossi" "Albachiara" "spotify:track:4P5Z3iEngFfaVe0qkv4Pdl" "Albachiara"
run "Ligabue" "Certe notti" "spotify:track:5gTuH5Jl8PVUitsFzFslWu" "Certe notti"
run "Tiziano Ferro" "Sere nere" "spotify:track:3ZRUL0W3SNJaySHwKVJYKs" "Sere nere"
run "Tiziano Ferro" "Xdono" "spotify:track:3h0v8BiwP1qzlCX7TXrsDF" "Xdono"
run "Negramaro" "Mentre tutto scorre" "spotify:track:4cbAhpXgxjpVazwfnWImsy" "Mentre tutto scorre"
run "Maneskin" "Zitti e buoni" "spotify:track:1lWWoec2z1j88GRblI5anV" "Zitti e buoni"
run "Maneskin" "Torna a casa" "spotify:track:3590AAEoqH50z4UmhMIY85" "Torna a casa"
run "Mahmood" "Soldi" "spotify:track:2dxky69jKzbtUfA3Hl8Nuo" "Soldi"
run "Mahmood" "Brividi" "spotify:track:1ZMGp9MTXbtAPvcKa0U3zS" "Brividi"
run "Marco Mengoni" "Due vite" "spotify:track:5htUUUBlgHZ9fztWTTDEFm" "Due vite"
run "Marco Mengoni" "Ti ho voluto bene veramente" "spotify:track:3z5eL4hQXaBWxSWZUTwI4e" "Ti ho voluto bene veramente"
run "Pinguini Tattici Nucleari" "Ringo Starr" "spotify:track:5yfE6GXTuJaAlepKoE0wJE" "Ringo Starr"
run "Pinguini Tattici Nucleari" "Giovani Wannabe" "spotify:track:0w1RIq0o6dzKPRexdeOmca" "Giovani Wannabe"
run "Ultimo" "I tuoi particolari" "spotify:track:0psAGFzUF64N2Ae4gMExzu" "I tuoi particolari"
run "Calcutta" "Paracetamolo" "spotify:track:4OQ1G3dyVx0SPUzBiWCNat" "Paracetamolo"
run "Calcutta" "Oroscopo" "spotify:track:2eGCc9zvaUKzd3oKwQNwXW" "Oroscopo"
run "Francesca Michielin" "Nessun grado di separazione" "spotify:track:7fLj1FFRJb7rVftDtYqa4c" "Nessun grado di separazione"
run "Elodie" "Andromeda" "spotify:track:537zln5lMSv7PEH3LrMTTh" "Andromeda"
run "Madame" "Marea" "spotify:track:3vLykknydhbDIVnaTvt1yJ" "Marea"

echo "=== All done! ==="
