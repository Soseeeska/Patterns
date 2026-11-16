#!/usr/bin/env bash
# Сборка TPO Zones для MotiveWave 7: dist/TpoZones-1.7.jar
#   MW_SDK=/путь/к/mwave_sdk.jar ./build.sh      (по умолчанию lib/mwave_sdk.jar)
# SDK берётся из установки MotiveWave или с motivewave.com/sdk; в репозиторий не кладётся.
# Без SDK сборка идёт против sdk-stubs/ — заглушек тех классов и методов SDK, которые вызывает индикатор
# (сигнатуры сняты с класса, собранного настоящим SDK; TpoZonesStudy.class получается побайтно тем же).
# В jar заглушки не попадают: в MotiveWave классы берутся из настоящего SDK. Новый вызов SDK в коде —
# добавить его в sdk-stubs с той же сигнатурой (класс / интерфейс, типы) и проверить сборкой с настоящим SDK.
# Байткод — Java 21, его загружает и MotiveWave 7, и более ранние версии на Java 21.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${MW_SDK:-lib/mwave_sdk.jar}"
rm -rf build && mkdir -p build/classes dist
if [ -f "$SDK" ]; then
  CP="$SDK"
else
  echo "нет $SDK — сборка против sdk-stubs/"
  mkdir -p build/stubs
  "${JAVAC:-javac}" --release 21 -d build/stubs $(find sdk-stubs -name '*.java')
  CP=build/stubs
fi
"${JAVAC:-javac}" --release 21 -encoding UTF-8 -cp "$CP" -d build/classes src/patterns/zones/*.java
jar --create --file dist/TpoZones-1.7.jar -C build/classes .
echo "dist/TpoZones-1.7.jar"
