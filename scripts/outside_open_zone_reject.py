"""Открытие RTH вне диапазона прошлого дня: удар о зону проторговки → цели прошлого дня.

Сценарий (зеркально для двух сторон):
  шорт — open выше High прошлого дня; зона проторговки выше open (или open
         внутри неё); цена в каком-то периоде дотягивается до зоны раньше, чем
         до High прошлого дня; дальше — идёт ли вниз к целям прошлого дня;
  лонг — open ниже Low; зона ниже open; удар раньше Low; дальше — вверх.
Зона — ближайшая к open с «внешней» стороны (с допуском touch_tolerance_ticks).
Цели (по порядку от open): граница диапазона (High/Low), ближний край VA
(VAH/VAL), POC, дальний край VA. Пробой зоны — два 30m close подряд за её
дальним краем раньше, чем цена дошла до границы диапазона.
Если удар и граница диапазона в одном баре — порядок неизвестен, день
считается отдельно («в одном баре»).

Два набора зон, доступных в день открытия: композиты баланса (VAH, VAL, POC
точкой и HVN-полоса) и первородные HVN-зоны. Сравнение: дни без удара и
фальш-зоны (композиты сдвинуты на 1–3 ширины VA целиком, первородные — на
1.5–3 своей ширины), 20 прогонов.

Запуск:  python -m scripts.outside_open_zone_reject [ES]
Результат: output/<INSTR>_outside_open_zone_reject.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo as primary_placebo
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands

LETTERS = "ABCDEFGHIJKLM"
TARGETS = ["range", "va_near", "poc", "va_far"]
T_NAME = {"range": "граница диапазона", "va_near": "VAH/VAL (ближний)", "poc": "POC", "va_far": "дальний край VA"}


def outside_days(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.sort_values("date").reset_index(drop=True)
    rows = []
    for i in range(1, len(d)):
        p, t = d.iloc[i - 1], d.iloc[i]
        if t.short_day or p.short_day:
            continue
        if t.open > p.high:
            side, tg = "шорт", {"range": p.high, "va_near": p.vah, "poc": p.poc, "va_far": p.val}
        elif t.open < p.low:
            side, tg = "лонг", {"range": p.low, "va_near": p.val, "poc": p.poc, "va_far": p.vah}
        else:
            continue
        rows.append({"date": t.date, "prev_date": p.date, "side": side, "open": t.open, **tg})
    return pd.DataFrame(rows)


def analyze(days: pd.DataFrame, bars: pd.DataFrame, zones: pd.DataFrame, tol: float) -> pd.DataFrame:
    """Для каждого дня: ближайшая внешняя зона, период удара, исходы."""
    by_day = {k: g.sort_values("period") for k, g in bars.groupby("date")}
    zd = zones.available_from.to_numpy(dtype="datetime64[ns]")
    zlo, zhi = zones.lo.to_numpy(), zones.hi.to_numpy()
    out = []
    for r in days.itertuples():
        g = by_day[r.date]
        H, L, C, P = g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy(), g.period.to_numpy()
        av = zd <= np.datetime64(r.date)
        short = r.side == "шорт"
        # ближайшая зона снаружи: для шорта — верх зоны ≥ open (зона выше или open внутри)
        cand = av & ((zhi + tol >= r.open) if short else (zlo - tol <= r.open))
        res = {"date": r.date, "side": r.side, "zone_idx": np.nan, "zone_dist": np.nan, "hit_period": np.nan,
               "same_bar": False, "broke": False}
        # первые касания целей
        first = {}
        for t in TARGETS:
            x = getattr(r, t)
            m = (L <= x) if short else (H >= x)
            first[t] = int(np.flatnonzero(m)[0]) if m.any() else None
        if cand.any():
            idx = np.flatnonzero(cand)
            dist = np.maximum((zlo[idx] - tol) - r.open, 0) if short else np.maximum(r.open - (zhi[idx] + tol), 0)
            j = idx[np.argmin(dist)]
            res["zone_idx"], res["zone_dist"] = j, float(dist.min())
            lo, hi = zlo[j] - tol, zhi[j] + tol
            touch = (H >= lo) if short else (L <= hi)
            if touch.any():
                h = int(np.flatnonzero(touch)[0])
                fr = first["range"]
                if fr is None or h < fr:
                    res["hit_period"] = int(P[h])
                    # пробой: 2 close подряд за дальним краем зоны раньше, чем цена дошла до границы
                    end = fr if fr is not None else len(C)
                    beyond = (C > zhi[j]) if short else (C < zlo[j])
                    for k in range(h, end - 1):
                        if beyond[k] and beyond[k + 1]:
                            res["broke"] = True
                            break
                elif h == fr:
                    res["same_bar"] = True
        for t in TARGETS:
            res[f"{t}_hit"] = first[t] is not None
            res[f"{t}_period"] = int(P[first[t]]) if first[t] is not None else np.nan
        out.append(res)
    return pd.DataFrame(out)


def summary(a: pd.DataFrame) -> dict:
    hit = a.hit_period.notna()
    s = {"n": len(a), "hit": hit.mean(), "n_hit": int(hit.sum()), "same_bar": a.same_bar.mean(),
         "broke_given_hit": a.broke[hit].mean() if hit.any() else np.nan}
    for t in TARGETS:
        s[f"{t}|hit"] = a[f"{t}_hit"][hit].mean() if hit.any() else np.nan
        s[f"{t}|nohit"] = a[f"{t}_hit"][~hit & ~a.same_bar].mean()
        reached = a[f"{t}_hit"]
        s[f"share_from_zone|{t}"] = (hit & reached).sum() / reached.sum() if reached.sum() else np.nan
    return s


def shift_bands(bands: pd.DataFrame, comps: pd.DataFrame, rng, cfg, tick) -> pd.DataFrame:
    pc = cfg["stats"]["placebo"]
    a, b = pc["shift_va_widths"]
    w = (comps.set_index("composite_id").vah - comps.set_index("composite_id").val).clip(
        lower=pc["min_va_width_ticks"] * tick)
    sh = {cid: np.round(rng.uniform(a, b) * w[cid] * rng.choice([-1, 1]) / tick) * tick
          for cid in bands.composite_id.unique()}
    out = bands.copy()
    s = out.composite_id.map(sh)
    out["lo"] += s
    out["hi"] += s
    return out


def main(instrument: str = "ES") -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick)
    zc = cfg["hvn_zones"]
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(dd, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)

    days = outside_days(daily)
    runs = cfg["stats"]["placebo"]["runs"]
    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    frames = []
    for src, zones, make_pl in (
            ("композиты баланса", bands, lambda: shift_bands(bands, comps, rng, cfg, tick)),
            ("первородные HVN-зоны", prim, lambda: primary_placebo(prim, rng, cfg["primary_zones"], tick, False))):
        print(f"\n{'=' * 25} {src} {'=' * 25}")
        for side in ("шорт", "лонг"):
            dsd = days[days.side == side]
            real = analyze(dsd, bars, zones, tol)
            frames.append(real.assign(source=src))
            rs = summary(real)
            ps = [summary(analyze(dsd, bars, make_pl(), tol)) for _ in range(runs)]

            def pl(key):
                v = np.array([p[key] for p in ps], float)
                v = v[~np.isnan(v)]
                lo, hi = np.percentile(v, [5, 95])
                mark = " ↑" if rs[key] > hi else (" ↓" if rs[key] < lo else "")
                return f"[фальш {v.mean():5.1%} ({lo:.0%}–{hi:.0%})]{mark}"

            where = "выше High" if side == "шорт" else "ниже Low"
            print(f"\n--- {side}: open {where} прошлого дня, дней {rs['n']} ---")
            print(f"Удар о зону раньше границы диапазона:      {rs['n_hit']:4d} = {rs['hit']:5.1%}  {pl('hit')}")
            print(f"  (удар и граница в одном баре: {rs['same_bar']:.1%})")
            print(f"После удара зона пробита (2 close за ней): {rs['broke_given_hit']:5.1%}  {pl('broke_given_hit')}")
            print("Цель достигнута за день:          после удара о зону | без удара | доля дней с ударом среди дошедших")
            for t in TARGETS:
                print(f"  {T_NAME[t]:20s}  {rs[f'{t}|hit']:6.1%} {pl(f'{t}|hit')}   "
                      f"{rs[f'{t}|nohit']:6.1%}   {rs[f'share_from_zone|{t}']:6.1%} {pl(f'share_from_zone|{t}')}")
            hp = real.hit_period.dropna().astype(int).map(lambda k: LETTERS[k]).value_counts()
            print("  период удара:", ", ".join(f"{k} {v}" for k, v in hp.reindex(list(LETTERS)).dropna().astype(int).items()))
    pd.concat(frames).to_csv(out / f"{instrument}_outside_open_zone_reject.csv", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
