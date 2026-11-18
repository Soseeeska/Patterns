# Прототипы (не в индикаторе)

`comp-sync-va.patch` — синхронизация композита и сессионных зон (обсуждалось 18.11.2026, не внедрено):

- узлы профиля композита (`compNodes`): тело (≥ 15% максимума вокруг POC), куски по провалам ≥ `-DsyncDip` (0.15) меньшей
  вершины, узел — строки ≥ (1 − `-DsyncDrop`) (0.75) вершины;
- `-DcompSync=true`: в каждом узле истинная — одна зона (наибольшее TPO композита на её строках, при равенстве старшая),
  синяя у ядра забирает свой узел; остальные в узле и зоны в провалах — минорные; решение по последней сессии на всю длину;
- `-DsyncVa=true`: плюс главная поддержка — зона под ядром, ближайшая к VAL композита, главное сопротивление — над ядром,
  ближайшая к VAH;
- `-DsyncHide=true`: минорные не рисуются (иначе — серые).

Применить на копии исходников и собрать инструменты:

```bash
git apply motivewave/parity/proto/comp-sync-va.patch     # на рабочей копии; откат — git checkout motivewave/src
javac -encoding UTF-8 -d /tmp/cls motivewave/src/patterns/zones/*.java motivewave/parity/*.java motivewave/parity/proto/NodesView.java
java -DcompSync=true -DsyncVa=true -DsyncHide=true -Djava.awt.headless=true -cp /tmp/cls Preview bars.csv out.png 50 0 0 comp 2024-02-21
java -Djava.awt.headless=true -cp /tmp/cls patterns.zones.NodesView bars.csv nodes.png 2024-02-21 2023-12-18   # профиль композита и узлы
```

`NodesView` — профиль композита, его узлы, VAL / VAH и зоны сессий (истинные / минорные / главные у краёв VA).
