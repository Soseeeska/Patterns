"""Отторжения обратно в диапазон и VA прошлого дня: сколько из них — прямо от зон.

Событие (30m-бар): хвост за уровнем прошлого RTH-дня и close обратно внутри:
  выше  — high > уровня, close ≤ уровня (уровень: High или VAH прошлого дня);
  ниже  — low  < уровня, close ≥ уровня (Low или VAL).
В каждой сессии берётся первое событие на каждый уровень.
«От зоны» — кончик хвоста (high / low бара) в зоне ± touch_tolerance_ticks.
Зоны, доступные на день события:
  * композиты баланса (этап 3): VAH, VAL, POC композита и HVN-полоса;
  * первородные HVN-зоны.
Доли сравниваются с фальш-зонами. Исход: дошла ли цена после события
до POC прошлого дня до конца сессии.

Запуск:  python -m scripts.prev_day_rejections [ES] [бары: ES_ETH] [с даты]
Два варианта: все события и строгие (open тоже внутри уровня).
Результат: output/<TAG>_prev_day_rejections.csv, output/<TAG>_prev_day_rejections_strict.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import placebo as primary_placebo
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted, load_test_bars, tag
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.intraday import build_bands, placebo_bands


def events(bars: pd.DataFrame, daily: pd.DataFrame, strict: bool = False) -> pd.DataFrame:
    """Первое отторжение на каждый уровень прошлого RTH-дня в каждой сессии.
    strict=True — open бара тоже внутри (наружу выходит только хвост); иначе
    сюда попадают и возвраты: бар открылся снаружи и закрылся внутри."""
    d = daily[~daily.short_day].sort_values("date")
    rth_dates = d.date.to_numpy()
    b = bars.sort_values("ts").reset_index(drop=True)
    sessions = np.sort(b.date.unique())
    out = []
    for j, (date, g) in enumerate(b.groupby("date", sort=True)):
        k = np.searchsorted(rth_dates, np.datetime64(date)) - 1     # последний RTH-день раньше сессии
        # он должен быть именно предыдущей сессией: иначе (нет RTH-данных, короткий день) пропуск
        if k < 0 or j == 0 or rth_dates[k] != sessions[j - 1]:
            continue
        p = d.iloc[k]
        O, H, L, C = g.open.to_numpy(), g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy()
        for level, price, side in (("High", p.high, "выше"), ("VAH", p.vah, "выше"),
                                   ("Low", p.low, "ниже"), ("VAL", p.val, "ниже")):
            m = (H > price) & (C <= price) if side == "выше" else (L < price) & (C >= price)
            if strict:
                m &= (O <= price) if side == "выше" else (O >= price)
            idx = np.flatnonzero(m)
            if not len(idx):
                continue
            i = idx[0]
            ext = H[i] if side == "выше" else L[i]
            after = slice(i + 1, len(g))
            to_poc = (L[after] <= p.poc).any() if side == "выше" else (H[after] >= p.poc).any()
            out.append({"date": date, "ts": g.ts.iloc[i], "level": level, "side": side, "level_price": price,
                        "extreme": ext, "prev_poc": p.poc, "reached_poc": bool(to_poc)})
    return pd.DataFrame(out)


def at_zone(ev: pd.DataFrame, zones: pd.DataFrame, tol: float, min_age_days: int = 0) -> np.ndarray:
    """Для каждого события — id зоны (первой подходящей), где кончик хвоста, или None.
    min_age_days — зона должна быть доступна хотя бы столько календарных дней
    (отсекает свежие зоны, которые почти совпадают с уровнями прошлого дня)."""
    zd = zones.available_from.to_numpy(dtype="datetime64[ns]") + np.timedelta64(min_age_days, "D")
    zlo, zhi = zones.lo.to_numpy() - tol, zones.hi.to_numpy() + tol
    ids = zones.zone_id.to_numpy()
    out = np.full(len(ev), None, dtype=object)
    for j, (dt, x) in enumerate(zip(ev.date.to_numpy(dtype="datetime64[ns]"), ev.extreme.to_numpy())):
        m = (zd <= dt) & (zlo <= x) & (zhi >= x)
        if m.any():
            out[j] = ids[np.flatnonzero(m)[0]]
    return out


def main(instrument: str = "ES", bars_instrument: str | None = None, since: str | None = None) -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    tol = cfg["interaction"]["touch_tolerance_ticks"] * tick
    zc, pc = cfg["hvn_zones"], cfg["primary_zones"]

    # зоны: композиты баланса и первородные HVN-зоны (все — по RTH-профилям)
    comps, comp_prof = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[comps.n_days >= cfg["zones"].get("min_composite_days", 0)]
    bands = build_bands(comps, comp_prof, cfg, tick)
    days = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    days["ok"] = days.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(days, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)

    tbars = load_test_bars(bars_instrument or instrument, since)[0] if (bars_instrument or since) else bars
    name = tag(instrument, bars_instrument, since)
    for strict in (False, True):
        print("\n" + "=" * 20, "СТРОГО: open тоже внутри, наружу только хвост" if strict else
              "ВСЕ: хвост за уровнем, close внутри (включая бары, открывшиеся снаружи)", "=" * 20)
        report(tbars, daily, bands, comps, prim, cfg, tick, tol, pc, strict,
               out / f"{name}_prev_day_rejections{'_strict' if strict else ''}.csv", name)


def report(tbars, daily, bands, comps, prim, cfg, tick, tol, pc, strict, path, name):
    ev = events(tbars, daily, strict)
    ev["comp_zone"] = at_zone(ev, bands, tol)
    ev["comp_type"] = ev.comp_zone.map(bands.set_index("zone_id").level_type)
    ev["prim_zone"] = at_zone(ev, prim, tol)
    ev.to_csv(path, index=False)

    real_levels = np.concatenate([comps.vah.to_numpy(), comps.val.to_numpy(), comps.poc.to_numpy()])
    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    runs = cfg["stats"]["placebo"]["runs"]
    AGE = 7
    ev_old_c = pd.notna(at_zone(ev, bands, tol, AGE))
    ev_old_p = pd.notna(at_zone(ev, prim, tol, AGE))
    p_comp, p_prim, p_comp_type, p_old_c, p_old_p = [], [], [], [], []
    for _ in range(runs):
        pb = placebo_bands(bands, comps, real_levels, rng, cfg, tick)
        hit = at_zone(ev, pb, tol)
        p_comp.append(pd.notna(hit).mean())
        p_old_c.append(pd.notna(at_zone(ev, pb, tol, AGE)).mean())
        p_comp_type.append(pd.Series(hit).map(pb.set_index("zone_id").level_type).value_counts() / len(ev))
        pp = primary_placebo(prim, rng, pc, tick, exclusive=False)
        p_prim.append(pd.notna(at_zone(ev, pp, tol)).mean())
        p_old_p.append(pd.notna(at_zone(ev, pp, tol, AGE)).mean())

    def band(vals):
        v = np.asarray(vals, float)
        return f"{v.mean():.1%} ({np.percentile(v, 5):.1%}–{np.percentile(v, 95):.1%})"

    print(f"{name}: событий {len(ev)} ({tbars.date.min():%Y-%m-%d} – {tbars.date.max():%Y-%m-%d}), "
          f"композитов {bands.composite_id.nunique()} ({len(bands)} зон), первородных зон {len(prim)}; "
          f"допуск ±{tol:g} п.\n")
    print("Событий по уровням:", ev.groupby(["level"]).size().to_dict())
    hc, hp = ev.comp_zone.notna(), ev.prim_zone.notna()
    print(f"\nОтторжение от зоны композита баланса: {hc.sum()} из {len(ev)} = {hc.mean():.1%}   "
          f"[фальш {band(p_comp)}]")
    ct = ev.comp_type.value_counts()
    pt = pd.concat(p_comp_type, axis=1).fillna(0).mean(axis=1)
    for t in ("VAH", "VAL", "POC", "HVN"):
        print(f"   по {t:3s}: {int(ct.get(t, 0)):4d} = {ct.get(t, 0) / len(ev):.1%}   [фальш {pt.get(t, 0):.1%}]")
    print(f"Отторжение от первородной HVN-зоны:  {hp.sum()} из {len(ev)} = {hp.mean():.1%}   "
          f"[фальш {band(p_prim)}]")
    print(f"От любой из них: {(hc | hp).sum()} = {(hc | hp).mean():.1%}")
    print(f"\nТолько зоны старше {AGE} дней (без свежих, совпадающих с уровнями прошлого дня):")
    print(f"   композит:    {ev_old_c.sum()} = {ev_old_c.mean():.1%}   [фальш {band(p_old_c)}]")
    print(f"   первородная: {ev_old_p.sum()} = {ev_old_p.mean():.1%}   [фальш {band(p_old_p)}]")

    print("\nПо уровням (доля от зоны композита / от первородной зоны):")
    for lv, g in ev.groupby("level"):
        print(f"   {lv:4s} n={len(g):4d}   композит {g.comp_zone.notna().mean():.1%}   "
              f"первородная {g.prim_zone.notna().mean():.1%}")

    print("\nИсход: цена дошла до POC прошлого дня до конца сессии")
    for nm, m in (("от зоны композита", hc), ("не от зоны композита", ~hc),
                  ("от первородной зоны", hp), ("не от первородной", ~hp)):
        print(f"   {nm:24s} n={m.sum():4d}   {ev.reached_poc[m].mean():.1%}")


if __name__ == "__main__":
    main(*sys.argv[1:])
