"""Сверка Java-движка с Python: зоны, события, подгонка, видимость по сессиям.

  ref.json  — python -m scripts.zone_map_export ES ES_ETH 250 ref.json
  java.json — java ParityDump bars.csv java.json [N подгонки]
  vis.json  — node vis_from_page.js ref.json vis.json [индекс шага подгонки]  (необязательно)

Запуск: python -I compare.py ref.json java.json [vis.json] [индекс шага]
"""
from __future__ import annotations

import json
import sys


def key(z):
    return (z["k"], round(z["lo"], 2), round(z["hi"], 2), z["av"])


def main(ref_path, java_path, vis_path=None, step=0):
    step = int(step)
    ref, jv = json.load(open(ref_path)), json.load(open(java_path))
    # Python берёт RTH-дни из RTH-файла (он может кончаться раньше ETH): события Java после него не сравниваем
    last = max(ref["rth"])
    jz = {}
    for z in jv["zones"]:
        z["L"] = [d for d in z["L"] if d <= last]
        z["S"] = [d for d in z["S"] if d <= last]
        z["ncf"] = sum(1 for d in z["cf"] if d <= last)
        jz.setdefault(key(z), []).append(z)
    miss, bad = [], []
    for z in ref["zones"]:
        c = jz.get(key(z))
        if not c:
            miss.append(z)
            continue
        j = c[0]
        errs = []
        if j["L"] != z["L"]:
            errs.append(f"L {len(j['L'])} vs {len(z['L'])}")
        if j["S"] != z["S"]:
            errs.append(f"S {len(j['S'])} vs {len(z['S'])}")
        if j["ncf"] != len(z["cf"]):
            errs.append(f"cf {j['ncf']} vs {len(z['cf'])}")
        if j["ai"] != z["ai"]:
            errs.append(f"ai {j['ai']} vs {z['ai']}")
        if ("gu" in z) != j["young"] or ("gu" in z and z["gu"] != j["gu"]):
            errs.append(f"gu {j['gu']} vs {z.get('gu', '-')}")
        ab = z["ab"][min(step, len(z["ab"]) - 1)]
        if [round(x, 2) for x in j["ab"]] != [round(x, 2) for x in ab]:
            errs.append(f"ab {j['ab']} vs {ab}")
        if errs:
            bad.append((key(z), errs))
    print(f"зон в эталоне: {len(ref['zones'])}, найдено в Java: {len(ref['zones']) - len(miss)}, "
          f"с расхождениями: {len(bad)}")
    for z in miss[:10]:
        print("  нет в Java:", key(z))
    for k, e in bad[:10]:
        print("  расхождение:", k, "; ".join(e))

    # предыдущий день и open сессии
    rs = {s["ord"]: s for s in ref["sessions"]}
    off = sum(1 for s in jv["sessions"] if s["ord"] in rs and
              (abs(s["vah"] - rs[s["ord"]]["vah"]) > 1e-6 or abs(s["poc"] - rs[s["ord"]]["poc"]) > 1e-6
               or abs(s["val"] - rs[s["ord"]]["val"]) > 1e-6))
    common = sum(1 for s in jv["sessions"] if s["ord"] in rs)
    print(f"сессий общих: {common} из {len(ref['sessions'])}, расхождений VAH/VAL/POC вчера: {off}")

    if vis_path:
        vis = json.load(open(vis_path))
        jvis = {s["ord"]: s["vis"] for s in jv["sessions"]}
        diff = 0
        for s, v in zip(ref["sessions"], vis):
            a = sorted((round(x[0], 2), round(x[1], 2), x[2], x[3]) for x in v)
            b = sorted((round(x[0], 2), round(x[1], 2), x[2], x[3]) for x in jvis.get(s["ord"], []))
            if a != b:
                diff += 1
                if diff <= 5:
                    print("  сессия", s["date"], "страница:", set(a) - set(b), "Java:", set(b) - set(a))
        print(f"видимость по сессиям: расхождений {diff} из {len(vis)}; "
              f"зон в последней сессии: страница {len(vis[-1])}, Java {len(jvis[ref['sessions'][-1]['ord']])}")


if __name__ == "__main__":
    main(*sys.argv[1:])
