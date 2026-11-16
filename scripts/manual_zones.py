"""Ручные зоны из графика MotiveWave (data/manual_zones_ESZ6.mwml) против баров, TPO и правил.

Файл — экспорт графика MotiveWave (JSON): фигуры Support Zone / Resistance Zone с ценами верха/низа,
цветом и временем точек (base36, мс UTC). Цвет — смысл зоны:
  зелёная — поддержка, красная — сопротивление, синяя — проторговка (ядро баланса),
  серая — промежуточная / мало информации, розовая (фиолетовая) — уровень прошлого ATH.
Цены на графике — фронт-контракт своего времени без склейки; наш ряд склеен под последний контракт,
поэтому зона сдвигается на сумму спредов роллов после её даты (rolls.switch_ts > время зоны).

Для каждой зоны:
  t0 — самая ранняя точка (откуда зона начата), сессия t0;
  match — торговалась ли цена в зоне за ±1 сессию от t0 (проверка сдвига);
  отношение к дневному профилю сессии t0 и предыдущей: POC / VA / HVN-полоса;
  отношение к композиту (close_in_range, ≥ 3 дней), закрытому до t0 или идущему в t0: ядро / тело;
  края зоны против краёв тел 30m-свечей (open/close) и хвостов за N сессий после t0 — против сдвинутых
  фальш-зон (± 1–3 ширины);
  отторжения (tpo/zone_classes.py) за N сессий после t0.

Запуск:  python -m scripts.manual_zones [data/manual_zones_ESZ6.mwml]
Результат: output/manual_zones.csv и сводка в консоли.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted, load_test_bars
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import hvn_band
from tpo.zone_classes import interaction_types

COLORS = {"0064003D": "зелёная", "8B00003D": "красная", "00158B3D": "синяя",
          "38373755": "серая", "EE82EE4F": "розовая"}
N_AFTER = 10            # сессий после начала зоны для тел / отторжений


def read_zones(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for g in d["graphs"]:
        for f in g.get("figures", []):
            if f.get("cid") not in ("support_zone", "resistance_zone"):
                continue
            pts = [c.split(",") for c in f["coords"]]
            ts = [pd.Timestamp(int(p[1], 36), unit="ms", tz="UTC") for p in pts]
            ps = [float(p[2]) for p in pts]
            rows.append({"id": f["id"], "color": COLORS.get(f["settings"].get("fill"), f["settings"].get("fill")),
                         "lo_raw": min(ps), "hi_raw": max(ps), "t0": min(ts), "t1": max(ts)})
    return pd.DataFrame(rows).sort_values("t0").reset_index(drop=True)


def edge_hits(o, h, l, c, edge, tick, tol=1):
    """Сколько баров: край тела (open/close) в edge ± tol тиков; из них с хвостом дальше через edge."""
    top, bot = np.maximum(o, c), np.minimum(o, c)
    nt = np.abs(top - edge) <= tol * tick + 1e-9
    nb = np.abs(bot - edge) <= tol * tick + 1e-9
    return int((nt | nb).sum())


def main(path=str(ROOT / "data" / "manual_zones_ESZ6.mwml")):
    cfg = load_config(instrument="ES")
    rbars, daily, profiles, rolls, tick = load_adjusted("ES", cfg)
    eth = load_test_bars("ES_ETH")[0].sort_values("ts").reset_index(drop=True)
    z = read_zones(path)
    sp = rolls.spread.to_numpy()
    swu = list(rolls.switch_ts.dt.tz_convert("UTC"))
    z["offset"] = [float(sp[[s > t for s in swu]].sum()) for t in z.t0]
    z["lo"], z["hi"] = z.lo_raw + z.offset, z.hi_raw + z.offset
    z["width"] = z.hi - z.lo

    ems = ((eth.ts.dt.tz_convert("UTC") - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
    sess = eth.date.to_numpy()
    O, H, L, C = (eth[k].to_numpy() for k in ("open", "high", "low", "close"))
    sdays = np.sort(eth.date.unique())
    first = pd.Series(np.arange(len(eth)), index=eth.date).groupby(level=0).min()
    last = pd.Series(np.arange(len(eth)), index=eth.date).groupby(level=0).max()

    full = daily[~daily.short_day].sort_values("date").reset_index(drop=True)
    comps, cprof = find_balances(rbars, daily, profiles, cfg, tick)
    comps = comps[(comps.end_reason != "open") & (comps.n_days >= 3)].reset_index(drop=True)
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    rng = np.random.default_rng(0)

    out = []
    for r in z.itertuples():
        i0 = int(np.searchsorted(ems, (r.t0 - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(milliseconds=1)))
        if i0 >= len(eth):
            continue
        s0 = sess[i0]
        k = int(np.searchsorted(sdays, s0))
        a, e = first[sdays[max(0, k - 1)]], last[sdays[min(len(sdays) - 1, k + 1)]] + 1
        traded = bool(((L[a:e] <= r.hi) & (H[a:e] >= r.lo)).any())
        dist = 0.0 if traded else float(min(np.abs(L[a:e] - r.hi).min(), np.abs(H[a:e] - r.lo).min()))
        # дневной профиль: сессия t0 (если RTH уже был) и предыдущий полный день
        prev = full[full.date < pd.Timestamp(s0)].iloc[-1]
        def rel(day):
            p = profiles[day.date]
            bl, bh = hvn_band(p, int(round(day.poc / tick)), cfg["intraday"]["hvn_share"])
            return {"poc_in": r.lo - tick <= day.poc <= r.hi + tick,
                    "in_va": r.lo >= day.val - tick and r.hi <= day.vah + tick,
                    "hvn_ov": max(0.0, min(r.hi, bh * tick) - max(r.lo, bl * tick)) / max(r.width, tick)}
        rp = rel(prev)
        # композит: последний закрытый до t0, или тот, в чьи дни попадает t0
        c_av = comps[comps.available_from <= pd.Timestamp(s0)]
        cinfo = {}
        if len(c_av):
            c = c_av.iloc[-1]
            mid = (r.lo + r.hi) / 2
            cinfo = {"comp": f"{c.start:%m-%d}..{c.end:%m-%d}", "in_comp": c.low <= mid <= c.high,
                     "vs_comp_poc": round(mid - c.poc, 2), "in_comp_va": c.val <= mid <= c.vah,
                     "comp_pos": round((mid - c.low) / max(c.high - c.low, tick), 2)}
        # тела и отторжения за N сессий после начала
        b0, b1 = first[s0], last[sdays[min(len(sdays) - 1, k + N_AFTER - 1)]] + 1
        o_, h_, l_, c_ = O[b0:b1], H[b0:b1], L[b0:b1], C[b0:b1]
        hits = edge_hits(o_, h_, l_, c_, r.lo, tick) + edge_hits(o_, h_, l_, c_, r.hi, tick)
        plc = []
        for _ in range(20):
            shv = rng.uniform(1, 3) * max(r.width, 2) * rng.choice([-1, 1])
            plc.append(edge_hits(o_, h_, l_, c_, r.lo + shv, tick) + edge_hits(o_, h_, l_, c_, r.hi + shv, tick))
        it = interaction_types(o_, h_, l_, c_, r.lo, r.hi, mw)
        out.append({"id": r.id, "color": r.color, "t0": r.t0.tz_convert("America/Chicago").strftime("%Y-%m-%d %H:%M"),
                    "session": pd.Timestamp(s0).strftime("%Y-%m-%d"), "lo": r.lo, "hi": r.hi, "width": r.width,
                    "offset": r.offset, "traded_pm1": traded, "dist": round(dist, 2),
                    "prev_poc_in": rp["poc_in"], "prev_in_va": rp["in_va"], "prev_hvn_ov": round(rp["hvn_ov"], 2),
                    **cinfo, "edge_hits": hits, "edge_hits_placebo": round(float(np.mean(plc)), 1),
                    "rej_long": int((it == "long").sum()), "rej_short": int((it == "short").sum()),
                    "range_bars": int((it == "range").sum())})
    df = pd.DataFrame(out)
    (ROOT / "output").mkdir(exist_ok=True)
    df.to_csv(ROOT / "output" / "manual_zones.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"зон {len(df)}; цена торговалась в зоне за ±1 сессию от начала: {df.traded_pm1.mean():.0%}")
    print(df.groupby("color").agg(n=("id", "size"), width=("width", "median"), traded=("traded_pm1", "mean"),
                                  edge=("edge_hits", "mean"), edge_plc=("edge_hits_placebo", "mean"),
                                  rej_l=("rej_long", "mean"), rej_s=("rej_short", "mean")).round(2))
    return df


if __name__ == "__main__":
    main(*sys.argv[1:])
