#!/usr/bin/env bash
# Batch-adds songs from the CSV that are missing from the DB.
# Already present (by Spotify URI):
#   spotify:track:4iRg9tHDgmRPvv56IC46Pl  (Группа крови – Кино)
#   spotify:track:12ELzrXluYapL20Uxqb45o  (Этот город – Bravo)
#   spotify:track:2m3PVx1gsVB5upxi94IW8I  (Жить в кайф – Max Korzh)

set -euo pipefail

PYTHON="/Users/seckintokcan/Documents/The Foundry/Active/Flowup/.venv/bin/python"
API="http://127.0.0.1:8000"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY=("$PYTHON" "$SCRIPT_DIR/generate_song_data.py" --lang ru --api-url "$API")

run() {
  local artist="$1" title="$2" uri="$3" display="$4"
  echo "=== Processing: $display ==="
  "${PY[@]}" --artist "$artist" --title "$title" --spotify-uri "$uri" --display-title "$display" || \
    echo "WARN: failed for $display, continuing..."
  echo ""
}

# CSV row 2 – Кино – Кукушка
run "Кино" "Кукушка" "spotify:track:1SCwVUbxOrqU7NADHm7x7j" "Кукушка"

# CSV row 3 – Кино – Звезда по имени Солнце
run "Кино" "Звезда по имени Солнце" "spotify:track:42m8JlHpUgaTkkQSqTnEoH" "Звезда по имени Солнце"

# CSV row 4 – Земфира – Хочешь?
run "Zemfira" "Hochesh" "spotify:track:3Zvo4oKr1SRCIgfmHAuQlA" "Хочешь?"

# CSV row 5 – Земфира – Искала
run "Zemfira" "Iskala" "spotify:track:4m81JBEYBpyKTaUsJdDMr9" "Искала"

# CSV row 6 – Би-2 – Полковнику никто не пишет
run "Би-2" "Полковнику никто не пишет" "spotify:track:24LuZI9xa1qH0rbhf4i6HA" "Полковнику никто не пишет"

# CSV row 7 – Би-2 – Варвара
run "Би-2" "Варвара" "spotify:track:3XoFQdFLGM1vBsFeP621DA" "Варвара"

# CSV row 8 – Сплин – Романс
run "Сплин" "Романс" "spotify:track:06vtYdnH8J9ItrbEc75YcI" "Романс"

# CSV row 9 – Сплин – Выхода нет
run "Сплин" "Выхода нет" "spotify:track:5eGeIWhWdLFlbCiuROoFwD" "Выхода нет"

# CSV row 10 – Наутилус Помпилиус – Я хочу быть с тобой
run "Наутилус Помпилиус" "Я хочу быть с тобой" "spotify:track:0mOA86xnvtUwXotaIuM9E6" "Я хочу быть с тобой"

# CSV row 11 – МакSим – Знаешь ли ты
run "МакSим" "Знаешь ли ты" "spotify:track:4bKw3g8eDSbrwPFOsbmPYO" "Знаешь ли ты"

# CSV row 12 – Монеточка – Каждый раз
run "Монеточка" "Каждый раз" "spotify:track:4IjiY0sShCYUEEoOSEwsgY" "Каждый раз"

# CSV row 13 – Время и Стекло – Имя 505
run "Время и Стекло" "Имя 505" "spotify:track:3H9TfxhAc9qVix9zff83JF" "Имя 505"

# CSV row 14 – Звери – Районы-кварталы
run "Звери" "Районы-кварталы" "spotify:track:46Y9Jp57brIX3pqfN7zd8W" "Районы-кварталы"

# CSV row 15 – Звери – До скорой встречи
run "Звери" "До скорой встречи" "spotify:track:1I5Y2sFwKJam9qkyegiWLG" "До скорой встречи"

# CSV row 16 – IOWA – Улыбайся
run "IOWA" "Улыбайся" "spotify:track:712K8qe0DjDKMtegKYPYI6" "Улыбайся"

# CSV row 17 – IOWA – Маршрутка
run "IOWA" "Маршрутка" "spotify:track:6qFpn2alCGtSGHCC09dgco" "Маршрутка"

# CSV row 18 – Molchat Doma – Судно
run "Molchat Doma" "Sudno" "spotify:track:1SHB1hp6267UK9bJQUxYvO" "Судно"

# CSV row 19 – polnalyubvi – Кометы
run "polnalyubvi" "Komety" "spotify:track:7CI5GaXYK9QwaowodWIbwX" "Кометы"

# CSV row 20 – ZOLOTO – Улицы ждали
run "ZOLOTO" "Ulitsy zhdali" "spotify:track:33roBRBTQ72Y197lGC6yh9" "Улицы ждали"

# CSV row 21 – Земляне – Трава у дома
run "Земляне" "Трава у дома" "spotify:track:7HBr5OwIsHxKfcD5tmsWxl" "Трава у дома"

# CSV row 22 – Алла Пугачёва – Миллион алых роз
run "Алла Пугачёва" "Миллион алых роз" "spotify:track:6hZKMAiaIepVtAtgFKm4xt" "Миллион алых роз"

# CSV row 23 – Алла Пугачёва – Позови меня с собой
run "Алла Пугачёва" "Позови меня с собой" "spotify:track:4hpEs4S57UaWS4IF2sm5yJ" "Позови меня с собой"

# CSV row 24 – Анна Герман – Надежда
run "Анна Герман" "Надежда" "spotify:track:3k694So9yoYZu2WHLTNIqU" "Надежда"

# CSV row 25 – Муслим Магомаев – Лучший город Земли
run "Муслим Магомаев" "Лучший город Земли" "spotify:track:1wTrFYYtquLQqtf6O432Q8" "Лучший город Земли"

# CSV row 26 – Эдуард Хиль – Зима
run "Эдуард Хиль" "Зима" "spotify:track:2NUGpsyBl6gUVNiGFn7Kmk" "Зима"

# CSV row 27 – Марк Бернес – Тёмная ночь
run "Марк Бернес" "Тёмная ночь" "spotify:track:2Rb0yD850QSpxXguuPH7pa" "Тёмная ночь"

# CSV row 28 – Михаил Боярский – Зеленоглазое такси
run "Михаил Боярский" "Зеленоглазое такси" "spotify:track:3kR987BLqTW75iaRlQ8Woh" "Зеленоглазое такси"

# CSV row 29 – Юрий Антонов – Крыша дома твоего
run "Юрий Антонов" "Крыша дома твоего" "spotify:track:43tz6W0EXDiAxLyg1doaku" "Крыша дома твоего"

# CSV row 31 – Баста – Сансара
run "Баста" "Сансара" "spotify:track:2pSfzuZd81zBaOA2LOKbdX" "Сансара"

# CSV row 32 – Баста – Выпускной
run "Баста" "Выпускной" "spotify:track:5Tct3O04Ozldk840VvjOPE" "Выпускной"

# CSV row 33 – Руки Вверх! – Крошка моя
run "Руки Вверх!" "Крошка моя" "spotify:track:4Dmthut5u5MlsPR1ZelAGg" "Крошка моя"

# CSV row 34 – Miyagi & Эндшпиль – I Got Love
run "Miyagi" "I Got Love" "spotify:track:5rwsJbBNa8SfPW4oFA1GjP" "I Got Love"

# CSV row 36 – Макс Корж – Горы по колено
run "Макс Корж" "Горы по колено" "spotify:track:7f6ALwMTMLAhXCfoHYLCsx" "Горы по колено"

# CSV row 37 – Noize MC – Вселенная бесконечна?
run "Noize MC" "Вселенная бесконечна" "spotify:track:5bkfPKnHLi6AUP3fJA5Xf5" "Вселенная бесконечна?"

# CSV row 38 – Дима Билан – Невозможное возможно
run "Дима Билан" "Невозможное возможно" "spotify:track:1j5Cqb6DiVS8YP2cL0NMkJ" "Невозможное возможно"

# CSV row 39 – Каста – Вокруг шум
run "Каста" "Вокруг шум" "spotify:track:5NqhAFTgPTsxhm7kDhfVCw" "Вокруг шум"

# CSV row 40 – Ёлка – Прованс
run "Ёлка" "Прованс" "spotify:track:5a8aSgJQmTJXrnAcEa1Kqv" "Прованс"

echo "=== All done! ==="
