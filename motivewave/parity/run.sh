#!/usr/bin/env bash
# Полная сверка Java-движка с Python и страницей карты зон (из корня репозитория: motivewave/parity/run.sh).
# Нужны: python + requirements.txt, JDK 21+, node.
set -euo pipefail
cd "$(dirname "$0")/../.."
T="${TMPDIR:-/tmp}/tpo_zones_parity"; mkdir -p "$T/cls"
python -m scripts.zone_map_export ES ES_ETH 250 "$T/ref.json"
python -m motivewave.parity.export_bars "$T/bars.csv"
javac -encoding UTF-8 -d "$T/cls" motivewave/src/patterns/zones/ZoneEngine.java motivewave/src/patterns/zones/ZonePlan.java \
  motivewave/parity/ParityDump.java motivewave/parity/Preview.java
# N подгонки → индекс шага в zone_align.map_check_steps [0,1,2,3,5,7,10,15,20,30,40,60]
for pair in "0 0" "3 3" "10 6" "20 8" "60 11"; do
  set -- $pair
  echo "== подгонка $1 сесс."
  java -cp "$T/cls" ParityDump "$T/bars.csv" "$T/java$1.json" "$1" >/dev/null
  node motivewave/parity/vis_from_page.js "$T/ref.json" "$T/vis$1.json" "$2" 3 >/dev/null
  python -I motivewave/parity/compare.py "$T/ref.json" "$T/java$1.json" "$T/vis$1.json" "$2"
done
java -Djava.awt.headless=true -cp "$T/cls" Preview "$T/bars.csv" motivewave/preview.png 5 0 5   # 5 сессий, топ-5 по отторжениям
