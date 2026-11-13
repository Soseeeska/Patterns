"""Признаки дня и события из торговых заметок (RTH, 30m).

Все определения — в config.patterns. Короткие дни пропускаются, «прошлый день» —
предыдущий полный день.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .intraday import run_tests


def day_features(bars: pd.DataFrame, days: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    pc = cfg["patterns"]
    d = days[days.day_type != "Short"].sort_values("date").reset_index(drop=True).copy()
    per = {k: g.sort_values("period").reset_index(drop=True) for k, g in bars.groupby("date")}
    ib = d.ib_high - d.ib_low
    d["ib_rel"] = ib / ib.rolling(pc["lookback_days"], min_periods=10).mean().shift(1)
    d["avg_range"] = (d.high - d.low).rolling(pc["lookback_days"], min_periods=10).mean().shift(1)
    d["ib_bucket"] = pd.cut(d.ib_rel, [0, *pc["ib_buckets"], np.inf],
                            labels=["узкий", "средний", "широкий"])
    rows = []
    for i in range(1, len(d)):
        t, p = d.iloc[i], d.iloc[i - 1]
        g = per[t.date]
        o = t.open
        # 1. открытие относительно прошлого дня
        if p.val <= o <= p.vah:
            loc = "в балансе (внутри VA)"
        elif p.low <= o <= p.high:
            loc = "дисбаланс в диапазоне"
        else:
            loc = "полный дисбаланс"
        side = "выше" if o > p.vah else ("ниже" if o < p.val else "внутри")
        # 3. период A: close внутри VA прошлого дня = принятие
        a = g.iloc[0]
        a_in = p.val <= a.close <= p.vah
        # 2. дошли ли до POC прошлого дня и в каком периоде
        hit = (g.low <= p.poc) & (g.high >= p.poc)
        poc_period = int(hit.idxmax()) if hit.any() else -1
        # 5. неудавшийся выход за IB
        ext = pc["ib_fail_min_ext_ticks"] * tick
        fail = ""
        fail_reach_opp = np.nan
        ibh, ibl = t.ib_high, t.ib_low
        for side_ib in ("up", "down"):
            beyond = (g.high >= ibh + ext) if side_ib == "up" else (g.low <= ibl - ext)
            beyond = beyond & (g.period >= 2)
            if not beyond.any():
                continue
            k0 = int(beyond.idxmax())
            back = g[(g.index > k0) & (g.close <= ibh if side_ib == "up" else g.index > k0) &
                     ((g.close <= ibh) if side_ib == "up" else (g.close >= ibl))]
            if len(back):
                kb = int(back.index[0])
                rest = g[g.index > kb]
                fail = side_ib
                fail_reach_opp = bool(((rest.low <= ibl) if side_ib == "up" else (rest.high >= ibh)).any())
                break
        # 6. последний период прошлого дня и гэп
        gp = per[p.date]
        m = gp.iloc[-1]
        m_dir = "вверх" if m.close > m.open else ("вниз" if m.close < m.open else "ноль")
        gap = o - p.close
        gap_kind = ("true gap" if (o > p.high or o < p.low) else
                    "гэп внутри диапазона" if abs(gap) >= pc["gap_min_ticks"] * tick else "без гэпа")
        gap_dir = "вверх" if gap > 0 else "вниз" if gap < 0 else "ноль"
        filled = bool(((g.low <= p.close) & (g.high >= p.close)).any()) if gap_kind != "без гэпа" else np.nan
        rows.append({
            "date": t.date, "open_loc": loc,
            "open_poc_dist": abs(o - p.poc) / t.avg_range if t.avg_range else np.nan, "open_side": side, "a_accept": "A принят (close в VA)" if a_in
            else "A отторгнут (close вне VA)",
            "poc_touched": poc_period >= 0, "poc_period": poc_period,
            "ib_fail": fail, "ib_fail_reach_opp": fail_reach_opp,
            "m_dir": m_dir, "gap_kind": gap_kind, "gap_dir": gap_dir, "gap_filled": filled,
            "m_with_gap": (m_dir == gap_dir) if gap_kind != "без гэпа" else np.nan,
            "day_dir": "вверх" if t.close > o else "вниз",
            "prev_migration": p.value_migration, "prev2_migration": d.iloc[i - 2].value_migration if i >= 2 else "",
        })
    f = pd.DataFrame(rows)
    keep = ["date", "day_type", "range_vs_avg", "range_vs_ib", "re_direction", "ib_rel", "ib_bucket",
            "value_migration", "profile_shape"]
    return f.merge(d[keep], on="date", how="left")


def rotation_breaks(bars: pd.DataFrame, days: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    """10. Период (не раньше min_period) закрылся за диапазоном всех прошлых периодов дня.
    Берётся первое такое событие в каждую сторону за день."""
    pc = cfg["patterns"]
    full = set(days[days.day_type != "Short"].date)
    rows = []
    for date, g in bars.groupby("date"):
        if date not in full:
            continue
        g = g.sort_values("period").reset_index(drop=True)
        seen = set()
        for k in range(pc["rotation_min_period"], len(g)):
            hi, lo = g.high[:k].max(), g.low[:k].min()
            c = g.close[k]
            for side, cond in (("вверх", c > hi), ("вниз", c < lo)):
                if not cond or side in seen:
                    continue
                seen.add(side)
                lvl = hi if side == "вверх" else lo
                rest = g.iloc[k + 1:]
                nxt_conf = bool(len(rest)) and bool((rest.close.iloc[0] > lvl) if side == "вверх"
                                                    else (rest.close.iloc[0] < lvl))
                day_close_beyond = (g.close.iloc[-1] > lvl) if side == "вверх" else (g.close.iloc[-1] < lvl)
                # возврат в середину диапазона до пробоя
                mid = (hi + lo) / 2
                back_mid = bool(((rest.low <= mid) if side == "вверх" else (rest.high >= mid)).any())
                rows.append({"date": date, "period": k, "side": side, "next_confirms": nxt_conf,
                             "day_close_beyond": bool(day_close_beyond), "back_to_mid": back_mid,
                             "last_period": k == len(g) - 1})
    return pd.DataFrame(rows)


def hidden_levels(bars, days, profiles, cfg, tick):
    """7. VAH/VAL/POC дня D, которые в течение следующих lookback дней «прошил»
    single print или хвост дня E (строка уровня в профиле E имеет 1 TPO).
    Уровень становится скрытым после E и тестируется по правилу этапа 6
    (берётся только первый тест). Базы сравнения:
      * обычные уровни: VAH/VAL/POC дня D, которые день E прошёл с ≥2 TPO;
      * чистые single prints: строки той же зоны single prints дня E, где нет уровня."""
    pc = cfg["patterns"]
    full = days[days.day_type != "Short"].sort_values("date").reset_index(drop=True)
    look = pc["hidden_lookback_days"]
    next_day = dict(zip(full.date[:-1], full.date[1:]))
    rows = []
    rng = np.random.default_rng(0)
    for i, src in full.iterrows():
        for lv in ("vah", "val", "poc"):
            L = int(round(src[lv] / tick))
            for j in range(i + 1, min(i + 1 + look, len(full))):
                e = full.iloc[j]
                p = profiles[e.date]
                if not (p.low <= L <= p.high):
                    continue
                cnt = p.counts[(L - p.low) // p.row]
                kind = "hidden (single print)" if cnt == 1 else "обычный (проторгован)"
                if e.date not in next_day:
                    break
                rows.append({"zone_id": f"{src.date:%Y%m%d}_{lv}", "level_type": lv.upper(), "lo": L * tick,
                             "hi": L * tick, "kind": kind, "composite_id": i, "composite_days": 1,
                             "available_from": next_day[e.date], "composite_end": e.date})
                if cnt == 1:
                    # чистый single print той же зоны: случайная строка с 1 TPO в ±20 тиках
                    sp = np.flatnonzero(p.counts == 1) * p.row + p.low
                    cand = sp[(np.abs(sp - L) >= 4) & (np.abs(sp - L) <= 20)]
                    if len(cand):
                        x = int(rng.choice(cand))
                        rows.append({"zone_id": f"{src.date:%Y%m%d}_{lv}_sp", "level_type": "SP",
                                     "lo": x * tick, "hi": x * tick, "kind": "чистый single print",
                                     "composite_id": i, "composite_days": 1,
                                     "available_from": next_day[e.date], "composite_end": e.date})
                break   # только первый день, который дошёл до уровня
    bands = pd.DataFrame(rows)
    t = run_tests(bars, bands, cfg, tick)
    first = t.sort_values(["zone_id", "date", "period"]).groupby("zone_id").head(1)
    return first.merge(bands[["zone_id", "kind"]], on="zone_id")
