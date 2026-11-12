"""Данные для интерактивной карты зон (artifact): 30m-свечи последних N сессий
и зоны с метриками силы на каждую сессию (только история до неё).

Зоны (по RTH-профилям): старые балансы (VA композитов), первородные HVN-зоны,
серые (незаполненные single prints). Метрики на сессию: тестов до (30m,
правила этапа 6, RTH-бары), баров-отторжений до (лонг / шорт), чистота
(доля отторжений в одну сторону), возраст в днях; у HVN — класс на дату.

Запуск:  python -m scripts.zone_map_export [ES] [ES_ETH] [sessions] [out.json]
Рядом с JSON пишется готовая страница (<out>.html) из scripts/zone_map_template.html.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from scripts.day_context import zone_map, hvn_class_table
from scripts.hvn_va_stats import per_day
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted, load_test_bars
from scripts.zone_wicks import analyze
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_context import grey_zones
from tpo.zone_classes import interaction_types


def main(instrument="ES", bars_instrument="ES_ETH", sessions="30", path=None):
    cfg = load_config(instrument=instrument)
    cc, zc = cfg["day_context"], cfg["hvn_zones"]
    share = cfg["wicks"]["class_rejection_share"]
    rbars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    comps, _ = find_balances(rbars, daily, profiles, cfg, tick)
    comps = comps[(comps.end_reason != "open") & (comps.n_days >= cfg["zones"].get("min_composite_days", 0))]
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(dd, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)
    grey = grey_zones(daily, profiles, tick, cc["grey_min_ticks"])
    cls_tab = hvn_class_table(rbars, prim, cfg["wicks"]["min_wick_ticks"] * tick, share)
    zm = zone_map(comps, prim, grey, cls_tab, share)

    b = load_test_bars(bars_instrument)[0].sort_values("ts").reset_index(drop=True)
    sess = np.sort(b.date.unique())[-int(sessions):]
    w = b[b.date.isin(sess)].reset_index(drop=True)
    lo_p, hi_p = w.low.min() - 40, w.high.max() + 40
    s_first, s_last = pd.Timestamp(sess[0]), pd.Timestamp(sess[-1])
    z = zm[(zm.available_from <= s_last) & (zm.filled.isna() | (zm.filled >= s_first))
           & (zm.hi >= lo_p) & (zm.lo <= hi_p)].reset_index(drop=True)
    z["zid"] = np.arange(len(z))

    # тесты и отторжения по RTH-барам — даты, чтобы считать «до сессии»
    zz = z.rename(columns={"zid": "zone_id_tmp"})
    zz = pd.DataFrame({"zone_id": z.zid, "lo": z.lo, "hi": z.hi, "available_from": z.available_from})
    _, tests, _ = analyze(rbars, zz, cfg, tick)
    rb = rbars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (rb[k].to_numpy() for k in ("open", "high", "low", "close"))
    D = rb.date.to_numpy()
    first = pd.Series(np.arange(len(rb)), index=rb.date).groupby(level=0).min()
    mw = cfg["wicks"]["min_wick_ticks"] * tick

    rth = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    rd = rth.date.to_numpy()
    zones_out = []
    for r in z.itertuples():
        tdates = np.sort(tests.date[tests.zone_id == r.zid].to_numpy(dtype="datetime64[ns]"))
        if r.available_from in first.index:
            s = int(first[r.available_from])
            t = interaction_types(O[s:], H[s:], L[s:], C[s:], r.lo, r.hi, mw)
            lg, sh = np.sort(D[s:][t == "long"]), np.sort(D[s:][t == "short"])
        else:
            lg = sh = np.array([], "datetime64[ns]")
        st = []
        for i, d in enumerate(sess):
            dn = np.datetime64(d)
            alive = r.available_from <= pd.Timestamp(d) and (pd.isna(r.filled) or r.filled >= pd.Timestamp(d))
            if not alive:
                st.append(None)
                continue
            nl, ns = int(np.searchsorted(lg, dn)), int(np.searchsorted(sh, dn))
            tot = nl + ns
            kind = r.type
            if kind == "hvn":
                kind = ("hvn_new" if tot == 0 else "hvn_S" if nl / tot >= share else
                        "hvn_R" if ns / tot >= share else "hvn_range")
            st.append([kind, int(np.searchsorted(tdates, dn)), tot,
                       round(max(nl, ns) / tot, 3) if tot else None,
                       int((pd.Timestamp(d) - r.available_from).days)])
        if any(x is not None for x in st):
            zones_out.append({"lo": float(r.lo), "hi": float(r.hi), "src": r.type, "st": st})

    # бары: время CT как «UTC», чтобы ось показывала Чикаго
    ts = w.ts.dt.tz_localize(None)
    bars_out = [[int(t.timestamp()), float(o), float(h), float(l), float(c), int(np.searchsorted(sess, d))]
                for t, o, h, l, c, d in zip(ts, w.open, w.high, w.low, w.close, w.date.to_numpy())]
    sess_out = []
    for d in sess:
        k = np.searchsorted(rd, np.datetime64(d)) - 1
        p = rth.iloc[k]
        sess_out.append({"date": f"{pd.Timestamp(d):%Y-%m-%d}", "prev": f"{p.date:%Y-%m-%d}",
                         "vah": float(p.vah), "val": float(p.val), "poc": float(p.poc),
                         "high": float(p.high), "low": float(p.low)})
    data = {"instrument": "ES (ESZ6, back-adjusted)", "bars": bars_out, "sessions": sess_out, "zones": zones_out,
            "rth_last": f"{rth.date.max():%Y-%m-%d}", "tick": tick}
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
