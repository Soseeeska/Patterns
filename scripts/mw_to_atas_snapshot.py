"""Рисунки MotiveWave (.mwml) → снапшот графика ATAS X (.cts), который импортируется в ATAS X как есть.

Формат .cts взят из снапшота, выгруженного из ATAS X 8.0.14 (data/atas/atas_x_sample.cts): JSON
OFT.Platform.Settings.Charting.ChartSettings, рисунки — Value.DrawingObjectses, прямоугольник —
OFT.Platform.DrawingObjects.RectangleObj. В нём: время точек _firstTime/_secondTime — UTC без зоны,
цена — TrueFirstPrice (пункты) и FirstPrice (тики, цена / 0.25), цвет — строка "A, R, G, B".
Новые рисунки — копии прямоугольника из образца с заменёнными полями; остальные настройки графика из образца.

Перенос:
  зоны и боксы  → прямоугольник: заливка — цвет и прозрачность MotiveWave; рамка и пунктирная средняя линия —
                  тот же оттенок светлее (HLS + 0.25), прозрачность рамки как у зон в образце (153);
                  продление вправо / влево — как в MW; скрытые в MW — Visible = false;
  горизонтали   → прямоугольник нулевой высоты: видна только рамка (цвет MW или серый, если в MW цвет темы);
  карандаш (pen) не переносится: в образце нет такого объекта (3 рисунка 2024 года на старом контракте).

Запуск:  python -m scripts.mw_to_atas_snapshot [data/manual_zones_ESZ6.mwml] [data/atas/atas_x_sample.cts]
Результат: data/atas/MW zones ESZ6.cts (только MotiveWave) и data/atas/MW zones ESZ6 + ATAS.cts
(MotiveWave + рисунки самого образца).
"""
from __future__ import annotations

import colorsys
import copy
import json
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from scripts.mw_to_atas import ROOT, convert

TICK = Decimal("0.25")
LINE_DEFAULT = (255, 200, 200, 200)          # цвет горизонтали, если в MW — цвет темы
BORDER_ALPHA = 153                           # рамка зоны: как у зон, нарисованных в ATAS (образец)
LIGHTEN = 0.25                               # рамка и середина: тот же оттенок, светлота HLS + 0.25


def _lighter(c: tuple[int, int, int, int], alpha: int) -> tuple[int, int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in c[1:]))
    r, g, b = colorsys.hls_to_rgb(h, min(0.85, l + LIGHTEN) if l < 0.98 else l, s)
    return (alpha, round(r * 255), round(g * 255), round(b * 255))
NUM = "\x00NUM:"                             # маркер числа, записываемого как есть (7651.00, 30585.0)


def _num(v) -> str:
    return f"{NUM}{v}\x00"


def _dump(obj) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return re.sub(r'"\\u0000NUM:([^\\]*)\\u0000"', r"\1", s)


def _load(path: Path):
    """JSON с числами-строками как в файле (decimal 7651.00 остаётся 7651.00)."""
    return json.loads(path.read_text(encoding="utf-8"),
                      parse_float=_num, parse_int=lambda s: int(s))


def _argb(c: str | None, alpha_override: int | None = None) -> tuple[int, int, int, int] | None:
    if not c or not c.startswith("#"):
        return None
    v = int(c[1:], 16)
    a, r, g, b = v >> 24 & 255, v >> 16 & 255, v >> 8 & 255, v & 255
    return (a if alpha_override is None else alpha_override, r, g, b)


def _cstr(c) -> str:
    return ", ".join(str(x) for x in c)


def _price(p: float) -> tuple[str, str]:
    d = Decimal(str(p)).quantize(Decimal("0.01"))
    t = d / TICK
    return _num(d), _num(t.quantize(Decimal("0.1")) if t == t.to_integral() else t.normalize())


def _time(t) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def make_rect(tpl: dict, r: dict) -> dict:
    o = copy.deepcopy(tpl)
    if r["kind"] == "rectangle":
        fill = _argb(r["fill_argb"])
        line = _lighter(fill, BORDER_ALPHA)      # в MW рамки не было — делаем светлее заливки
        hi_first = True
    else:                                         # горизонталь
        line = _argb(r["border_argb"]) or LINE_DEFAULT
        fill = (0,) + line[1:]
        hi_first = True
    p_hi, t_hi = _price(r["price_high"])
    p_lo, t_lo = _price(r["price_low"])
    o.update({
        "ExtendLeft": bool(r["extend_left"]),
        "ExtendRight": bool(r["extend_right"]),
        "AreaColor": _cstr(fill),
        "AreaOpacility": max(0, round(fill[0] / 2.55) - 1),   # в образце: альфа 26 ↔ 9
        "Color": _cstr(line),
        "Width": 1,
        "Style": "solid",
        "TrueFirstPrice": p_hi if hi_first else p_lo,
        "TrueSecondPrice": p_lo if hi_first else p_hi,
        "_firstTime": _time(r["t1"]),
        "_secondTime": _time(r["t2"]),
        "FirstPrice": t_hi if hi_first else t_lo,
        "SecondPrice": t_lo if hi_first else t_hi,
        "FirstPos": -1,
        "SecondPos": -1,
        "Id": uuid.uuid5(uuid.NAMESPACE_URL, "mw:" + r["id"]).hex,
        "Selected": False,
        "MouseOver": False,
        "Pressed": False,
        "IsUnderCursor": False,
        "Visible": not r["hidden"],
        "Locked": bool(r["locked"]),
    })
    mid = o["MiddleLine"]
    mid["Enabled"] = r["kind"] == "rectangle"
    if mid["Enabled"]:                            # средняя линия зоны — пунктир цвета рамки
        a, rr, gg, bb = line
        mid["Value"]["Color"] = f"Color [A={a}, R={rr}, G={gg}, B={bb}]"
        mid["Value"]["Width"] = 1
    return o


def main(mwml: str = str(ROOT / "data" / "manual_zones_ESZ6.mwml"),
         sample: str = str(ROOT / "data" / "atas" / "atas_x_sample.cts")):
    sample = Path(sample)
    snap = _load(sample)
    objs = snap["Value"]["DrawingObjectses"]
    tpl = next(o for o in objs if o["$type"].startswith("OFT.Platform.DrawingObjects.RectangleObj,"))

    rows = [r for r in convert(Path(mwml)) if r["kind"] in ("rectangle", "horizontal_line")]
    rows.sort(key=lambda r: r["t1"])
    mw = [make_rect(tpl, r) for r in rows]

    dst = ROOT / "data" / "atas"
    for name, figs in (("MW zones ESZ6.cts", mw), ("MW zones ESZ6 + ATAS.cts", objs + mw)):
        out = copy.deepcopy(snap)
        out["Value"]["DrawingObjectses"] = figs
        (dst / name).write_text(_dump(out), encoding="utf-8")
        print(f"{dst / name}: {len(figs)} рисунков")


if __name__ == "__main__":
    main(*sys.argv[1:])
