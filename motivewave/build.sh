#!/usr/bin/env bash
# Сборка TPO Zones для MotiveWave 7: dist/TpoZones-<VERSION>.jar
#   MW_SDK=/путь/к/mwave_sdk.jar ./build.sh      (по умолчанию lib/mwave_sdk.jar)
# SDK берётся из установки MotiveWave или с motivewave.com/sdk; в репозиторий не кладётся.
# Без SDK сборка идёт против sdk-stubs/ — заглушек тех классов и методов SDK, которые вызывает индикатор
# (сигнатуры сняты с класса, собранного настоящим SDK). В jar заглушки не попадают: в MotiveWave классы берутся из
# настоящего SDK. Новый вызов SDK в коде — добавить его в sdk-stubs с той же сигнатурой (класс / интерфейс, типы) и
# проверить сборкой с настоящим SDK.
# Пакет версии (с 1.8.1): в репозитории код в пакете patterns.zones (его же используют инструменты parity/), а в jar —
# в patterns.zones.v<версия> (1.8.1 → patterns.zones.v1_8_1). У jar разных версий разные классы: MotiveWave грузит их
# рядом, а не берёт класс из того jar, что загрузился первым (из-за этого после перезапуска «откатывалась» версия).
# Сборка — дата и коммит (с пометкой dirty, если есть незакоммиченные правки) — подставляется в TpoZonesStudy.BUILD и
# видна в строке info индикатора.
# Байткод — Java 21, его загружает и MotiveWave 7, и более ранние версии на Java 21.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=1.8.2
PKG="patterns.zones.v${VERSION//./_}"
STAMP="$(date +%F) $(git rev-parse --short HEAD 2>/dev/null || echo nogit)$(git diff --quiet HEAD -- . 2>/dev/null || echo -dirty)"
SDK="${MW_SDK:-lib/mwave_sdk.jar}"
rm -rf build && mkdir -p build/classes build/src dist
for f in src/patterns/zones/*.java; do
  sed -e "s/^package patterns\.zones;/package ${PKG};/" \
      -e "s/^import patterns\.zones\./import ${PKG}./" \
      -e "s/static final String BUILD = \"dev\";/static final String BUILD = \"${STAMP}\";/" "$f" > "build/src/$(basename "$f")"
done
grep -q "BUILD = \"${STAMP}\"" build/src/TpoZonesStudy.java || { echo "не подставилась сборка в TpoZonesStudy.BUILD"; exit 1; }
if [ -f "$SDK" ]; then
  CP="$SDK"
else
  echo "нет $SDK — сборка против sdk-stubs/"
  mkdir -p build/stubs
  "${JAVAC:-javac}" --release 21 -d build/stubs $(find sdk-stubs -name '*.java')
  CP=build/stubs
fi
"${JAVAC:-javac}" --release 21 -encoding UTF-8 -cp "$CP" -d build/classes build/src/*.java
JAR="dist/TpoZones-${VERSION}.jar"
jar --create --file "$JAR" -C build/classes .
echo "$JAR  ($PKG, сборка $STAMP)"
