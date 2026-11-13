"""Данные и страница интерактивной карты зон: 30m-свечи последних N сессий
и все зоны в их ценовом диапазоне с датами событий.

Зоны (по RTH-профилям): старые балансы (VA композитов), первородные HVN-зоны,
серые (незаполненные single prints). Для каждой зоны хранятся даты (в днях
от 1970-01-01):
  av — когда стала доступна, ai — её номер в списке RTH-дней, fl — когда
       проторговалась (серые) или null;
  t  — тесты (30m, правила этапа 6, RTH-бары), L / S — бары-отторжения
       хвостом вверх (лонг) / вниз (шорт);
  cf — зачётные HVN-дни (8–10 TPO) после появления: [дата, зазор до зоны, п.]
       (0 — полоса HVN пересекла зону), до 10 п.
По ним страница считает состояние зоны на любой день: тесты, отторжения,
чистоту, класс HVN-зоны, подтверждения, возраст — в том числе «паспорт»
после N торговых дней проверки.

Запуск:  python -m scripts.zone_map_export [ES] [ES_ETH] [sessions=250] [out.json]
Рядом с JSON пишется готовая страница (<out>.html) из scripts/zone_map_template.html.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from scripts.day_context import zone_map
from scripts.hvn_va_stats import per_day
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted, load_test_bars
from scripts.zone_wicks import analyze
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_context import grey_zones
from tpo.zone_classes import interaction_types

KIND = {"old_balance": 0, "hvn": 1, "grey": 5}
MAX_NEAR = 10.0                      # подтверждения дальше 10 п. от зоны не хранятся
EPOCH = np.datetime64("1970-01-01", "D")


def ordinal(x) -> int:
    return int((np.datetime64(x, "D") - EPOCH).astype(int))


def build_zones(cfg, rbars, daily, profiles, tick):
    """Карта зон (без классов: класс страница считает по датам отторжений)."""
    cc, zc = cfg["day_context"], cfg["hvn_zones"]
    comps, _ = find_balances(rbars, daily, profiles, cfg, tick)
    comps = comps[(comps.end_reason != "open") & (comps.n_days >= cfg["zones"].get("min_composite_days", 0))]
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(dd, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)
    grey = grey_zones(daily, profiles, tick, cc["grey_min_ticks"])
    return zone_map(comps, prim, grey), dd[dd.ok]


def main(instrument="ES", bars_instrument="ES_ETH", sessions="250", path=None):
    cfg = load_config(instrument=instrument)
    rbars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    zm, qual = build_zones(cfg, rbars, daily, profiles, tick)

    b = load_test_bars(bars_instrument)[0] if bars_instrument != instrument else rbars
    b = b.sort_values("ts").reset_index(drop=True)
    sess = np.sort(b.date.unique())[-int(sessions):]
    w = b[b.date.isin(sess)].reset_index(drop=True)
    lo_p, hi_p = w.low.min() - 40, w.high.max() + 40
    s_first, s_last = pd.Timestamp(sess[0]), pd.Timestamp(sess[-1])
    z = zm[(zm.available_from <= s_last) & (zm.filled.isna() | (zm.filled >= s_first))
           & (zm.hi >= lo_p) & (zm.lo <= hi_p)].reset_index(drop=True)

    # события по RTH-барам: тесты и бары-отторжения с датами
    zt = pd.DataFrame({"zone_id": np.arange(len(z)), "lo": z.lo, "hi": z.hi, "available_from": z.available_from})
    _, tests, _ = analyze(rbars, zt, cfg, tick)
    rb = rbars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (rb[k].to_numpy() for k in ("open", "high", "low", "close"))
    D = rb.date.to_numpy()
    first = pd.Series(np.arange(len(rb)), index=rb.date).groupby(level=0).min()
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    rth = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    rth_ord = [ordinal(d) for d in rth.date]
    q_date = qual.date.to_numpy(dtype="datetime64[ns]")
    q_lo, q_hi = qual.band_lo.to_numpy(), qual.band_hi.to_numpy()

    zones_out = []
    for i, r in z.iterrows():
        t_ord = sorted({ordinal(d) for d in tests.date[tests.zone_id == i]})
        lg = sh = []
        if r.available_from in first.index:
            s = int(first[r.available_from])
            it = interaction_types(O[s:], H[s:], L[s:], C[s:], r.lo, r.hi, mw)
            lg = [ordinal(d) for d in D[s:][it == "long"]]
            sh = [ordinal(d) for d in D[s:][it == "short"]]
        m = q_date >= np.datetime64(r.available_from)
        gap = np.maximum.reduce([np.zeros(m.sum()), q_lo[m] - r.hi, r.lo - q_hi[m]])
        cf = [[ordinal(d), round(float(g), 2)] for d, g in zip(q_date[m], gap) if g <= MAX_NEAR]
        av = ordinal(r.available_from)
        zones_out.append({"lo": float(r.lo), "hi": float(r.hi), "k": KIND[r.type], "av": av,
                          "ai": int(np.searchsorted(rth_ord, av)),
                          "fl": None if pd.isna(r.filled) else ordinal(r.filled),
                          "t": t_ord, "L": lg, "S": sh, "cf": cf})

    # бары: время CT как «UTC», чтобы ось показывала Чикаго
    ts = w.ts.dt.tz_localize(None)
    bars_out = [[int(t.timestamp()), float(o), float(h), float(l), float(c), int(np.searchsorted(sess, d))]
                for t, o, h, l, c, d in zip(ts, w.open, w.high, w.low, w.close, w.date.to_numpy())]
    rd = rth.date.to_numpy()
    sess_out = []
    for d in sess:
        p = rth.iloc[np.searchsorted(rd, np.datetime64(d)) - 1]
        sess_out.append({"date": f"{pd.Timestamp(d):%Y-%m-%d}", "ord": ordinal(d), "prev": f"{p.date:%Y-%m-%d}",
                         "vah": float(p.vah), "val": float(p.val), "poc": float(p.poc)})
    data = {"instrument": "ES (ESZ6, back-adjusted)", "tick": tick, "share": cfg["wicks"]["class_rejection_share"],
            "rth_last": f"{rth.date.max():%Y-%m-%d}", "rth": rth_ord,
            "bars": bars_out, "sessions": sess_out, "zones": zones_out}

    path = path or str(ROOT / "output" / "zone_map_data.json")
    raw = json.dumps(data, separators=(",", ":"))
    with open(path, "w") as f:
        f.write(raw)
    tpl = (ROOT / "scripts" / "zone_map_template.html").read_text(encoding="utf-8")
    with open(path.rsplit(".", 1)[0] + ".html", "w", encoding="utf-8") as f:
        f.write(tpl.replace("__DATA__", raw.replace("</", "<\\/")))
    print(path, len(bars_out), "баров,", len(zones_out), "зон")


if __name__ == "__main__":
    main(*sys.argv[1:])
