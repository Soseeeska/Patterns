#!/usr/bin/env bash
# Картинки индикатора для README (из корня репозитория: motivewave/parity/previews.sh). Нужны: python + requirements.txt, JDK 21+.
# Бары — несклеенные, как в MotiveWave (export_bars --raw); настройки — как у индикатора по умолчанию.
set -euo pipefail
cd "$(dirname "$0")/../.."
T="${TMPDIR:-/tmp}/tpo_zones_previews"; mkdir -p "$T/cls" output motivewave/img
python -m motivewave.parity.export_bars "$T/bars_raw.csv" --raw
python -m scripts.manual_zones >/dev/null
javac -encoding UTF-8 -d "$T/cls" motivewave/src/patterns/zones/ZoneEngine.java motivewave/src/patterns/zones/ZonePlan.java \
  motivewave/parity/Bench.java motivewave/parity/Preview.java
P="java -Djava.awt.headless=true -cp $T/cls Preview $T/bars_raw.csv"
M="-Dmanual=output/manual_zones_bench_raw.csv"
$P motivewave/img/preview.png 12                                   # последние сессии
$P motivewave/img/aug2026.png 21 0 0 comp 2026-08-31               # баланс 20–26.08.2026: своё ядро (расчёт в ценах графика)
java -Djava.awt.headless=true $M -cp "$T/cls" Preview "$T/bars_raw.csv" motivewave/img/jun2026_manual.png 9 0 0 comp 2026-06-12
java -Djava.awt.headless=true $M -cp "$T/cls" Preview "$T/bars_raw.csv" motivewave/img/jun2024_manual.png 12 0 0 comp 2024-07-03
java -Djava.awt.headless=true -Dgrid=true -cp "$T/cls" Preview "$T/bars_raw.csv" motivewave/img/grid_jun2026.png 3 0 0 comp 2026-06-12
java -Dstdout.encoding=UTF-8 -cp "$T/cls" Bench "$T/bars_raw.csv" output/manual_zones_bench_raw.csv
