"""Рисунки графика MotiveWave (.mwml) → нейтральный файл для переноса в ATAS X.

Из экспорта берутся все фигуры: зоны (support_zone / resistance_zone), боксы, горизонтальные линии
и карандаш (pen). Индикаторы графика (TPO, профиль-статистика) — не рисунки, пропускаются.

MotiveWave пишет цвет как RRGGBBAA, ATAS показывает и хранит ARGB — здесь цвет переводится в #AARRGGBB.
Время точек в .mwml — мс UTC в base36; в файле — UTC и биржевое время (America/Chicago).
Цены — как на графике MotiveWave (ESZ6, без склейки), т.е. совпадут с графиком ESZ6 в ATAS.

Запуск:  python -m scripts.mw_to_atas [data/manual_zones_ESZ6.mwml]
Результат: data/atas/<имя>.csv (прямоугольники и линии) и data/atas/<имя>.json (всё, включая pen).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")
MEANING = {"006400": "зелёная (поддержка)", "8B0000": "красная (сопротивление)",
           "00158B": "синяя (проторговка)", "383737": "серая (промежуточная)",
           "EE82EE": "розовая (прошлый ATH)", "FFFFFF": "белая"}


def argb(rrggbbaa: str | None) -> str | None:
    """'0064003D' → '#3D006400'; 'EE82EE' (без альфы) → '#FFEE82EE'."""
    if not rrggbbaa:
        return None
    s = rrggbbaa.upper()
    return "#" + (s[6:8] if len(s) == 8 else "FF") + s[:6]


def ts(b36: str) -> datetime:
    return datetime.fromtimestamp(int(b36, 36) / 1000, timezone.utc)


def line_color(spec: str | None) -> str | None:
    """Поле line MotiveWave: 'N' — без линии, ',,EE82EE' — цвет третьим полем, пусто — цвет темы."""
    if spec is None:
        return None
    parts = spec.split(",")
    if parts[0] == "N":
        return "none"
    return argb(parts[2]) if len(parts) > 2 and parts[2] else None


def convert(path: Path) -> list[dict]:
    figs = json.load(open(path, encoding="utf-8"))["graphs"][0]["figures"]
    out = []
    for f in figs:
        cid, st = f.get("cid"), f.get("settings", {})
        if cid is None:                      # индикатор на графике
            continue
        row = {"id": f["id"], "mw_type": cid, "label": f.get("label", ""), "hidden": bool(f.get("hidden")),
               "locked": bool(f.get("locked"))}
        if cid in ("support_zone", "resistance_zone", "box"):
            pts = [c.split(",") for c in f["coords"]]
            t = sorted(ts(p[1]) for p in pts)
            p = sorted(float(x[2]) for x in pts)
            fill = st.get("fill")
            row.update(kind="rectangle", t1=t[0], t2=t[1], price_low=p[0], price_high=p[1],
                       fill_argb=argb(fill), border_argb=line_color(st.get("line")),
                       extend_right=bool(st.get("extended")), extend_left=bool(st.get("extendLeft")),
                       meaning=MEANING.get((fill or "")[:6].upper(), ""))
        elif cid == "horizontal_line":
            pts = [c.split(",") for c in f["coords"]]
            t = sorted(ts(x[1]) for x in pts)
            row.update(kind="horizontal_line", t1=t[0], t2=t[1], price_low=float(pts[0][2]),
                       price_high=float(pts[0][2]), fill_argb=None,
                       border_argb=line_color(st.get("line")) or "theme default",
                       extend_right=bool(st.get("extended")), extend_left=bool(st.get("extendLeft")),
                       meaning="")
        elif cid == "pen":
            pts = [c.split(",") for c in st["coords"].split(":")]
            row.update(kind="polyline", points=[(ts(a).isoformat(), float(b)) for a, b in pts],
                       border_argb=line_color(st.get("line")) or "theme default")
        else:
            row.update(kind="unknown", raw=f)
        out.append(row)
    return out


def main(path: str = str(ROOT / "data" / "manual_zones_ESZ6.mwml")):
    path = Path(path)
    rows = convert(path)
    dst = ROOT / "data" / "atas"
    dst.mkdir(parents=True, exist_ok=True)

    cols = ["id", "kind", "mw_type", "meaning", "fill_argb", "border_argb", "price_low", "price_high",
            "t1_utc", "t2_utc", "t1_ct", "t2_ct", "extend_right", "extend_left", "hidden", "label"]
    flat = [r for r in rows if r["kind"] in ("rectangle", "horizontal_line")]
    flat.sort(key=lambda r: (r["kind"], r["t1"]))
    with open(dst / f"{path.stem}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        for r in flat:
            w.writerow({**r, "t1_utc": f"{r['t1']:%Y-%m-%d %H:%M:%S}", "t2_utc": f"{r['t2']:%Y-%m-%d %H:%M:%S}",
                        "t1_ct": f"{r['t1'].astimezone(CT):%Y-%m-%d %H:%M:%S}",
                        "t2_ct": f"{r['t2'].astimezone(CT):%Y-%m-%d %H:%M:%S}"})

    def enc(o):
        return o.isoformat() if isinstance(o, datetime) else str(o)
    json.dump({"source": path.name, "instrument": "ESZ6", "color_format": "#AARRGGBB", "figures": rows},
              open(dst / f"{path.stem}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=enc)

    from collections import Counter
    print(f"{len(rows)} фигур → {dst}/{path.stem}.csv / .json")
    for (k, c), n in sorted(Counter((r["kind"], r.get("fill_argb") or r.get("border_argb")) for r in rows).items(),
                            key=str):
        print(f"  {k:16s} {c!s:16s} {n}")


if __name__ == "__main__":
    main(*sys.argv[1:])
