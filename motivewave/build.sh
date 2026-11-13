#!/usr/bin/env bash
# Сборка TPO Zones для MotiveWave 7: dist/TpoZones-1.1.jar
#   MW_SDK=/путь/к/mwave_sdk.jar ./build.sh      (по умолчанию lib/mwave_sdk.jar)
# SDK берётся из установки MotiveWave или с motivewave.com/sdk; в репозиторий не кладётся.
# SDK для MotiveWave 7 собран под Java 24 — нужен JDK 24+ (JAVAC=/путь/к/javac, если javac в PATH старее).
# Байткод — Java 21, его загружает и MotiveWave 7, и более ранние версии на Java 21.
set -euo pipefail
cd "$(dirname "$0")"
SDK="${MW_SDK:-lib/mwave_sdk.jar}"
[ -f "$SDK" ] || { echo "нет $SDK — укажите MW_SDK=/путь/к/mwave_sdk.jar"; exit 1; }
rm -rf build && mkdir -p build/classes dist
"${JAVAC:-javac}" --release 21 -encoding UTF-8 -cp "$SDK" -d build/classes src/patterns/zones/*.java
jar --create --file dist/TpoZones-1.1.jar -C build/classes .
echo "dist/TpoZones-1.1.jar"
