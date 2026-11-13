"""Сегодняшний RTH-день против вчерашнего (локально) и против карты зон (глобально).

Строка = полный RTH-день, у которого предыдущий день тоже полный.
Признаки по моментам решения:
  вчера   — тип дня, форма, миграция value к позавчера, спайк в M, хвосты, poor high/low;
  open    — где open относительно вчера, относительно вчерашнего спайка, в какой зоне;
  после A — где close A относительно вчерашнего VA;
  после IB — ширина IB к среднему за 20 дней, где IB относительно вчерашнего VA.
Карта зон на день (tpo/day_context.py): старые балансы (VA композитов), HVN-зоны
с классом на дату, серые зоны (незаполненные single prints).
Исходы дня: вчерашние цели, где сформировался POC дня (в какой зоне или вне
всех — новый баланс), ближайшая зона сверху/снизу (дошли / остановились /
прошли), заполнение серых зон.
Всё про зоны сравнивается со сдвинутыми зонами (1.5–3 ширины, ширина ≥ 4 п.).
Таблицы — отдельно до и после split_date.

Раздел 0 — признаки из заметок «MP FP»: Expansion Potential (Balance open /
Disbalance in range / Full disbalance), принятие/отторжение в A, ширина IB,
фейл экстеншин, последние полчаса (M) и open к вчерашнему close,
долгосрочные / краткосрочные хвосты.

Запуск:  python -m scripts.day_context [ES] [1|0 — считать ли зоны (долго)]
Результат: output/<INSTR>_day_context.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.hvn_va_stats import per_day
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_context import (bucket_rel, grey_zones, loc_vs_va, open_location, spike, tail_bucket)
from tpo.day_type import classify_days
from tpo.zone_classes import interaction_types

OPEN_ORDER = ["выше High", "VAH–High", "внутри VA", "Low–VAL", "ниже Low"]
ZTYPES = ["old_balance", "hvn_S", "hvn_R", "hvn_range", "hvn_new", "grey"]
ZNAME = {"old_balance": "старый баланс (VA композита)", "hvn_S": "HVN · поддержка", "hvn_R": "HVN · сопротивление",
         "hvn_range": "HVN · проторговка", "hvn_new": "HVN · без истории", "grey": "серая (single prints)"}


# ---------------------------------------------------------------- карта зон
def hvn_class_table(bars, zones, mw, share):
    """Для каждой HVN-зоны: даты баров-отторжений (лонг / шорт) — чтобы считать класс на дату."""
    b = bars.sort_values("ts").reset_index(drop=True)
    O, H, L, C = (b[k].to_numpy() for k in ("open", "high", "low", "close"))
    D = b.date.to_numpy()
    first = pd.Series(np.arange(len(b)), index=b.date).groupby(level=0).min()
    out = {}
    for z in zones.itertuples():
        if z.available_from not in first.index:
            out[z.zone_id] = (np.array([], "datetime64[ns]"), np.array([], "datetime64[ns]"))
            continue
        s = int(first[z.available_from])
        t = interaction_types(O[s:], H[s:], L[s:], C[s:], z.lo, z.hi, mw)
        out[z.zone_id] = (np.sort(D[s:][t == "long"]), np.sort(D[s:][t == "short"]))
    return out


def hvn_class_on(cls_tab, zone_id, date, share):
    lg, sh = cls_tab[zone_id]
    nl = np.searchsorted(lg, np.datetime64(date))      # строго раньше дня
    ns = np.searchsorted(sh, np.datetime64(date))
    tot = nl + ns
    if tot == 0:
        return "hvn_new"
    if nl / tot >= share:
        return "hvn_S"
    if ns / tot >= share:
        return "hvn_R"
    return "hvn_range"


def zone_map(comps, prim, grey):
    """Единая таблица зон: type, lo, hi, available_from, filled (NaT — не заполняется), zone_id."""
    ob = pd.DataFrame({"type": "old_balance", "lo": comps.val, "hi": comps.vah,
                       "available_from": comps.available_from, "filled": pd.NaT, "zone_id": -1})
    hv = pd.DataFrame({"type": "hvn", "lo": prim.lo, "hi": prim.hi, "available_from": prim.available_from,
                       "filled": pd.NaT, "zone_id": prim.zone_id})
    gr = pd.DataFrame({"type": "grey", "lo": grey.lo, "hi": grey.hi, "available_from": grey.available_from,
                       "filled": grey.filled, "zone_id": -1})
    z = pd.concat([ob, hv, gr], ignore_index=True)
    return z[z.available_from.notna()].reset_index(drop=True)


def active(z, date, cls_tab, share):
    a = z[(z.available_from <= date) & (z.filled.isna() | (z.filled >= date))].copy()
    m = a.type == "hvn"
    a.loc[m, "type"] = [hvn_class_on(cls_tab, i, date, share) for i in a.zone_id[m]]
    return a


def shift_zones(z, rng, tick):
    w = np.maximum(z.hi - z.lo, 4.0)
    sh = np.round(rng.uniform(1.5, 3.0, len(z)) * w * rng.choice([-1, 1], len(z)) / tick) * tick
    out = z.copy()
    out["lo"] = z.lo + sh
    out["hi"] = z.hi + sh
    return out


# ---------------------------------------------------------------- признаки и исходы
def zone_part(day, a, tol):
    """Зонные признаки (на open) и исходы дня для одного дня и набора активных зон."""
    o, H, L, poc = day["open"], day["high"], day["low"], day["poc"]
    r = {}
    inside = a[(a.lo - tol <= o) & (a.hi + tol >= o)]
    r["open_in"] = ",".join(sorted(set(inside.type))) or "вне зон"
    r["open_in_old_balance"] = bool((inside.type == "old_balance").any())
    in_poc = a[(a.lo - tol <= poc) & (a.hi + tol >= poc)]
    for t in ZTYPES:
        r[f"poc_in_{t}"] = bool((in_poc.type == t).any())
    r["poc_in_any"] = len(in_poc) > 0
    up = a[a.lo - tol > o].sort_values("lo")
    dn = a[a.hi + tol < o].sort_values("hi", ascending=False)
    for side, s in (("up", up), ("dn", dn)):
        if len(s):
            z = s.iloc[0]
            r[f"{side}_type"] = z.type
            r[f"{side}_dist"] = (z.lo - o) if side == "up" else (o - z.hi)
            reach = H >= z.lo - tol if side == "up" else L <= z.hi + tol
            through = H > z.hi + tol if side == "up" else L < z.lo - tol
            r[f"{side}_reached"] = bool(reach)
            r[f"{side}_stopped"] = bool(reach and not through)
        else:
            r[f"{side}_type"], r[f"{side}_dist"] = "нет", np.nan
            r[f"{side}_reached"] = r[f"{side}_stopped"] = False
    return r


def build(bars, daily, profiles, cfg, tick, zmap, cls_tab, zones_only=False):
    cc = cfg["day_context"]
    share = cfg["wicks"]["class_rejection_share"]
    tol = cc["zone_tolerance_ticks"] * tick
    d = daily.sort_values("date").reset_index(drop=True)
    by_day = {k: g.sort_values("period") for k, g in bars.groupby("date")}
    ib_avg = (d.ib_high - d.ib_low).where(~d.short_day).rolling(20, min_periods=10).mean().shift(1)
    rows = []
    for i in range(2, len(d)):
        y, t = d.iloc[i - 1], d.iloc[i]
        if t.short_day or y.short_day:
            continue
        g = by_day[t.date]
        r = {"date": t.date}
        a = active(zmap, t.date, cls_tab, share)
        r.update(zone_part(t, a, tol))
        if not zones_only:
            # вчера
            sp, sp_lo, sp_hi = spike(by_day[y.date], cc["spike_min_ticks"] * tick)
            r.update({"y_type": y.day_type, "y_shape": y.profile_shape, "y_migration": y.value_migration,
                      "y_spike": sp, "y_tail_up": tail_bucket(y.tail_up_ticks, cc["long_tail_ticks"]),
                      "y_tail_dn": tail_bucket(y.tail_down_ticks, cc["long_tail_ticks"]),
                      "y_poor_high": bool(y.poor_high), "y_poor_low": bool(y.poor_low)})
            # хвосты по заметкам: долгосрочный — день закрылся на противоположной стороне от хвоста
            mn = cfg["tpo"]["tail_min_rows"] * cfg["tpo"]["price_row_ticks"]
            cpos = (y.close - y.low) / (y.high - y.low) if y.high > y.low else 0.5
            r["y_tail_up_kind"] = ("нет" if y.tail_up_ticks < mn else
                                   "долгосрочный" if cpos <= 0.5 else "краткосрочный")
            r["y_tail_dn_kind"] = ("нет" if y.tail_down_ticks < mn else
                                   "долгосрочный" if cpos >= 0.5 else "краткосрочный")
            nxt = d.iloc[i:i + 5]
            r["y_high_held5"] = bool(len(nxt) == 5 and nxt.high.max() <= y.high)
            r["y_low_held5"] = bool(len(nxt) == 5 and nxt.low.min() >= y.low)
            # последние полчаса вчера (M) и open относительно вчерашнего close
            M = by_day[y.date].iloc[-1]
            r["y_m"] = ("M лонговый" if M.close > M.open else "M шортовый" if M.close < M.open else "M доджи")
            r["y_m_vs_poc"] = "close M выше POC" if M.close > y.poc else "close M ниже POC"
            r["gap"] = "open выше close вчера" if t.open > y.close else "open ниже close вчера"
            r["day_up"] = bool(t.close > t.open)
            # open
            r["open_loc"] = open_location(t.open, y)
            r["open_cat"] = {"внутри VA": "Balance open", "VAH–High": "Disbalance in range",
                             "Low–VAL": "Disbalance in range"}.get(r["open_loc"], "Full disbalance")
            if sp == "none":
                r["open_vs_spike"] = "нет спайка"
            else:
                r["open_vs_spike"] = ("выше спайка" if t.open > sp_hi else "ниже спайка" if t.open < sp_lo
                                      else "внутри спайка")
            # A и IB
            A = g[g.period == 0].iloc[0]
            ib = g[g.period < cfg["tpo"]["ib_periods"]]
            r["a_close_vs_va"] = loc_vs_va(A.close, A.close, y)
            r["a_accept"] = "A: принятие (close в VA)" if r["a_close_vs_va"] == "внутри VA" else "A: отторжение (close вне VA)"
            # расширение: RE за IB (> 15% IB), диапазон к среднему, порядок расширений
            ibh, ibl = ib.high.max(), ib.low.min()
            ext = cfg["day_type"]["min_extension_share_of_ib"] * (ibh - ibl)
            rest = g[g.period >= cfg["tpo"]["ib_periods"]]
            up_p = rest.period[rest.high > ibh + ext]
            dn_p = rest.period[rest.low < ibl - ext]
            r["re_up"], r["re_dn"] = len(up_p) > 0, len(dn_p) > 0
            r["re_any"] = r["re_up"] or r["re_dn"]
            r["re_both"] = r["re_up"] and r["re_dn"]
            if r["re_both"]:
                first_up = up_p.min() < dn_p.min()
                # фейл экстеншин: закрылись за IB со стороны второго расширения
                r["failed_ext_reversal"] = bool(t.close < ibl) if first_up else bool(t.close > ibh)
            r["range_rel"] = t.range_vs_avg
            r["expansion"] = bool(t.range_vs_avg > 1.0) if not np.isnan(t.range_vs_avg) else np.nan
            r["ib_vs_va"] = loc_vs_va(ib.low.min(), ib.high.max(), y)
            r["ib_width"] = bucket_rel((ib.high.max() - ib.low.min()) / ib_avg[i], *cc["ib_width_bands"])
            # исходы: вчерашние цели
            r.update({"hit_y_poc": bool(t.low <= y.poc <= t.high), "hit_y_vah": bool(t.high >= y.vah),
                      "hit_y_val": bool(t.low <= y.val), "took_y_high": bool(t.high > y.high),
                      "took_y_low": bool(t.low < y.low), "migration": t.value_migration,
                      "day_type": t.day_type, "shape": t.profile_shape})
            if sp != "none":
                base = sp_lo if sp == "up" else sp_hi     # основание спайка
                r["spike_base_broken"] = bool(t.low < base) if sp == "up" else bool(t.high > base)
                r["spike_extended"] = bool(t.high > sp_hi) if sp == "up" else bool(t.low < sp_lo)
        rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- отчёт
def pct(s):
    return f"{s.mean():5.1%}" if len(s) else "   — "


def table(df, by, cols, names, split):
    print(f"\n{'':24s} " + "  ".join(f"{n:>12s}" for n in names) + "      n (до / после)")
    for key, g in df.groupby(by, sort=False):
        pre, post = g[g.date < split], g[g.date >= split]
        cells = "  ".join(f"{pct(pre[c])}/{pct(post[c])}" for c in cols)
        print(f"{str(key):24s} {cells}   {len(pre):4d} / {len(post):3d}")


def main(instrument: str = "ES", zones: str = "1") -> None:
    cfg = load_config(instrument=instrument)
    cc, zc = cfg["day_context"], cfg["hvn_zones"]
    out = ROOT / "output"
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    daily = classify_days(daily, profiles, cfg, tick)
    comps, _ = find_balances(bars, daily, profiles, cfg, tick)
    comps = comps[(comps.end_reason != "open") & (comps.n_days >= cfg["zones"].get("min_composite_days", 0))]
    dd = per_day(daily, profiles, tick, cfg["intraday"]["hvn_share"]).sort_values("date")
    dd["ok"] = dd.hvn_tpo.between(zc["min_tpo"], zc["max_tpo"])
    prim = zone_frame(dd, daily, cfg, tick)
    prim = prim[prim.available_from.notna()].reset_index(drop=True)
    grey = grey_zones(daily, profiles, tick, cc["grey_min_ticks"])
    mw = cfg["wicks"]["min_wick_ticks"] * tick
    share = cfg["wicks"]["class_rejection_share"]
    cls_tab = hvn_class_table(bars, prim, mw, share)
    zmap = zone_map(comps, prim, grey)
    split = pd.Timestamp(cc["split_date"])

    df = build(bars, daily, profiles, cfg, tick, zmap, cls_tab)
    df.to_csv(out / f"{instrument}_day_context.csv", index=False)
    print(f"{instrument}: дней {len(df)} ({df.date.min():%Y-%m-%d} – {df.date.max():%Y-%m-%d}); "
          f"до {split:%Y-%m-%d} — {(df.date < split).sum()}, после — {(df.date >= split).sum()}")
    print(f"Зоны: старых балансов {len(comps)}, HVN {len(prim)}, серых {len(grey)} "
          f"(заполнено {grey.filled.notna().sum()})")
    print("В каждой ячейке: до split / после split.")

    print("\n===== 0. ПО ЗАМЕТКАМ «MP FP» =====")
    ex = ["re_any", "re_both", "expansion", "hit_y_poc"]
    exn = ["RE за IB", "RE в обе", "диапазон > ср.", "POC вчера"]
    print("\nExpansion Potential: тип открытия → расширение дня")
    cat_order = ["Balance open", "Disbalance in range", "Full disbalance"]
    df["open_cat"] = pd.Categorical(df.open_cat, cat_order)
    table(df.sort_values("open_cat"), "open_cat", ex, exn, split)
    print("\nRejection / Acceptance в A: тип открытия × A → расширение дня")
    table(df.sort_values(["open_cat", "a_accept"]).assign(k=lambda x: x.open_cat.astype(str) + " · " + x.a_accept),
          "k", ex, exn, split)
    print("\nIB: ширина → расширение")
    table(df[df.ib_width != "?"], "ib_width", ["re_any", "re_both", "expansion"], exn[:3], split)
    both = df[df.re_both == True]
    print(f"\nФейл экстеншин (RE в обе стороны, {len(both)} дней): закрылись за IB со стороны второго расширения")
    table(both.assign(k="все"), "k", ["failed_ext_reversal"], ["разворот"], split)
    print("\nПоследние полчаса вчера (M) и open относительно вчерашнего close → день вверх / снят High / снят Low")
    table(df.assign(k=df.y_m + " · " + df.gap), "k", ["day_up", "took_y_high", "took_y_low"],
          ["день вверх", "снят High", "снят Low"], split)
    table(df.assign(k=df.y_m_vs_poc + " · " + df.gap), "k", ["day_up", "took_y_high", "took_y_low"],
          ["день вверх", "снят High", "снят Low"], split)
    print("\nХвосты по заметкам (долгосрочный — день закрылся на противоположной стороне от хвоста)")
    table(df, "y_tail_up_kind", ["took_y_high", "y_high_held5"], ["High снят завтра", "High цел 5 дн"], split)
    table(df, "y_tail_dn_kind", ["took_y_low", "y_low_held5"], ["Low снят завтра", "Low цел 5 дн"], split)

    tg = ["hit_y_poc", "hit_y_vah", "hit_y_val", "took_y_high", "took_y_low"]
    tn = ["POC вчера", "VAH вчера", "VAL вчера", "снят High", "снят Low"]
    print("\n===== 1. ЛОКАЛЬНО: сегодня против вчера =====")
    print("\nOpen относительно вчера → цели за день")
    df["open_loc"] = pd.Categorical(df.open_loc, OPEN_ORDER)
    table(df.sort_values("open_loc"), "open_loc", tg, tn, split)
    print("\nПосле IB: где IB относительно вчерашнего VA → цели")
    table(df, "ib_vs_va", tg, tn, split)
    print("\nПосле IB: ширина IB → цели")
    table(df, "ib_width", tg, tn, split)
    print("\nПосле A: close A относительно вчерашнего VA → цели")
    table(df, "a_close_vs_va", tg, tn, split)
    sp = df[df.y_spike != "none"]
    print(f"\nВчера спайк в M ({len(sp)} дней): open относительно спайка → основание спайка пробито / спайк продолжен / POC вчера")
    table(sp.assign(k=sp.y_spike + " · " + sp.open_vs_spike), "k",
          ["spike_base_broken", "spike_extended", "hit_y_poc"], ["основание", "продолжение", "POC вчера"], split)
    print("\nВчерашний хвост по длине (≥ long_tail_ticks — длинный) → снят ли вчерашний экстремум")
    table(df, "y_tail_up", ["took_y_high", "hit_y_poc"], ["снят High", "POC вчера"], split)
    table(df, "y_tail_dn", ["took_y_low", "hit_y_poc"], ["снят Low", "POC вчера"], split)
    print("\nВчера poor high → снят ли вчерашний High")
    table(df, "y_poor_high", ["took_y_high"], ["снят High"], split)
    print("\nВчера poor low → снят ли вчерашний Low")
    table(df, "y_poor_low", ["took_y_low"], ["снят Low"], split)

    if zones == "0":
        return
    print("\n===== 2. ГЛОБАЛЬНО: зоны (реальные против сдвинутых) =====")
    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    plac = []
    for _ in range(cc["placebo_runs"]):
        pz = shift_zones(zmap, rng, tick)
        plac.append(build(bars, daily, profiles, cfg, tick, pz, cls_tab, zones_only=True))

    def cmp(name, f):
        v = [f(p) for p in plac]
        r = f(df)
        lo, hi = np.nanpercentile(v, [5, 95])
        mark = " ↑" if r > hi else (" ↓" if r < lo else "")
        rpre, rpost = f(df[df.date < split]), f(df[df.date >= split])
        print(f"  {name:58s} {r:6.1%}  [фальш {np.nanmean(v):5.1%} ({lo:.1%}–{hi:.1%})]{mark}"
              f"   до/после {rpre:.1%}/{rpost:.1%}")

    print("\nГде сформировался POC дня:")
    for t in ZTYPES:
        cmp(f"POC в зоне «{ZNAME[t]}»", lambda x, t=t: x[f"poc_in_{t}"].mean())
    cmp("POC вне всех зон (новый баланс)", lambda x: 1 - x.poc_in_any.mean())
    print("\nOpen внутри старого баланса → POC дня там же:")
    cmp("open в старом балансе: POC в старом балансе",
        lambda x: x.poc_in_old_balance[x.open_in_old_balance].mean())
    cmp("open вне старого баланса: POC в старом балансе",
        lambda x: x.poc_in_old_balance[~x.open_in_old_balance].mean())
    print("\nБлижайшая зона по ходу (сверху / снизу от open):")
    for side, nm in (("up", "сверху"), ("dn", "снизу")):
        cmp(f"дошли до ближайшей зоны {nm}", lambda x, s=side: x[f"{s}_reached"][x[f"{s}_type"] != "нет"].mean())
        cmp("  из дошедших — экстремум дня остановился в зоне", lambda x, s=side: x[f"{s}_stopped"][x[f"{s}_reached"]].mean())
        for t in ZTYPES:
            cmp(f"  … зона «{ZNAME[t]}»: остановился (из дошедших)",
                lambda x, s=side, t=t: x[f"{s}_stopped"][x[f"{s}_reached"] & (x[f"{s}_type"] == t)].mean())

    print("\n===== 3. ВМЕСТЕ: open относительно вчера × open в старом балансе → цели =====")
    df["k"] = df.open_loc.astype(str) + " · " + np.where(df.open_in_old_balance, "в старом балансе", "вне")
    table(df.sort_values(["open_loc", "k"]), "k", ["hit_y_poc", "poc_in_old_balance", "up_reached", "dn_reached"],
          ["POC вчера", "POC в ст.бал.", "зона сверху", "зона снизу"], split)


if __name__ == "__main__":
    main(*sys.argv[1:])
