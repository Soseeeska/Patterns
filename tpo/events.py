"""Таблица событий: касания зон прошлых композитов (строка = день × зона).

Определения (config.interaction):
  * зона доступна с available_from композита включительно (без look-ahead);
  * касание: уровень в [low − tol, high + tol] хотя бы одного периода дня;
  * подход: сторона open дня относительно уровня (open == уровень → по close
    предыдущего дня); gap_through — open и вчерашний close по разные стороны;
  * принятие (acceptance.mode = "periods"): ≥ N подряд периодов (начиная с
    периода касания) целиком по дальнюю сторону уровня; иначе — отказ;
  * acceptance.mode = "poc_close": учитываются только входы в композит снаружи
    (VAH сверху, VAL снизу); принятие — close дня в POC композита (± допуск)
    или дальше POC от стороны входа; иначе — отказ;
  * противоположная граница: VAH → VAL, VAL → VAH, POC → VAL при подходе сверху,
    VAH при подходе снизу. Проверяется с периода касания до конца дня (d0)
    и дальше на 1 и 3 полных дня (кумулятивно);
  * MFE / MAE — от уровня в сторону отказа / против неё, с периода касания до
    конца дня (бар касания целиком: внутри бара порядок high/low неизвестен).
  * breakout_day — касание в день, который закрыл баланс. Такие события
    смещены (баланс закрылся именно из-за этого дня) и в основной
    статистике исключаются (config.interaction.exclude_breakout_day).
Короткие дни пропускаются.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_zones(comps: pd.DataFrame, levels: list[str], min_days: int = 0) -> pd.DataFrame:
    closed = comps[(comps["end_reason"] != "open") & (comps["n_days"] >= min_days)]
    rows = []
    for c in closed.itertuples():
        for lv in levels:
            rows.append({
                "zone_id": f"{c.composite_id}_{lv}",
                "composite_id": c.composite_id,
                "composite_start": c.start,
                "composite_end": c.end,
                "composite_days": c.n_days,
                "available_from": c.available_from,
                "level_type": lv,
                "level_price": getattr(c, lv.lower()),
                "comp_vah": c.vah,
                "comp_val": c.val,
                "comp_poc": c.poc,
            })
    return pd.DataFrame(rows)


def _run_end(mask: np.ndarray, n: int) -> int:
    """Индекс, на котором впервые набирается n подряд True, или -1."""
    run = 0
    for i, m in enumerate(mask):
        run = run + 1 if m else 0
        if run >= n:
            return i
    return -1


def build_events(bars: pd.DataFrame, days: pd.DataFrame, comps: pd.DataFrame,
                 rolls: pd.DataFrame, cfg: dict, tick: float) -> pd.DataFrame:
    icfg = cfg["interaction"]
    tol = icfg["touch_tolerance_ticks"] * tick
    n_acc = icfg["acceptance"]["periods_beyond"]
    mode = icfg["acceptance"].get("mode", "periods")
    poc_tol = icfg["acceptance"].get("poc_tolerance_ticks", 2) * tick
    horizons = icfg["opposite_boundary_horizon_days"]
    zones = build_zones(comps, cfg["zones"]["levels"], cfg["zones"].get("min_composite_days", 0))

    d = days[days["day_type"] != "Short"].sort_values("date").reset_index(drop=True)
    d["atr"] = (d["high"] - d["low"]).rolling(20, min_periods=10).mean().shift(1)
    per = {k: g.sort_values("period") for k, g in bars.groupby("date")}
    roll_dates = rolls["roll_date"].sort_values().to_numpy()

    touches = {z: 0 for z in zones["zone_id"]}
    accepted_before = {z: False for z in zones["zone_id"]}
    zp = zones["level_price"].to_numpy()
    avail = zones["available_from"].to_numpy()

    rows = []
    for i, day in d.iterrows():
        date = day["date"]
        active = avail <= np.datetime64(date)
        hit = active & (zp >= day["low"] - tol) & (zp <= day["high"] + tol)
        idx = np.flatnonzero(hit)
        if not len(idx):
            continue
        g = per[date]
        lows, highs = g["low"].to_numpy(), g["high"].to_numpy()
        prev_close = d.at[i - 1, "close"] if i > 0 else np.nan
        future = d.iloc[i + 1:i + 1 + max(horizons)]
        for k in idx:
            z = zones.iloc[k]
            L = z.level_price
            assert z.available_from <= date, "look-ahead: зона использована раньше срока"
            if day["open"] != L:
                above = day["open"] > L
            else:
                above = prev_close > L
            gap_through = (not np.isnan(prev_close)) and ((prev_close > L) != (day["open"] > L)) \
                and day["open"] != L and prev_close != L
            t_mask = (lows - tol <= L) & (highs + tol >= L)
            t0 = int(np.argmax(t_mask))
            if mode == "poc_close":
                # только вход в композит снаружи: VAH сверху, VAL снизу
                entering = (z.level_type == "VAH" and above) or (z.level_type == "VAL" and not above)
                if not entering:
                    touches[z.zone_id] += 1
                    continue
                # принятие = день закрылся в POC (± допуск) или дальше POC от стороны входа
                accepted = (day["close"] <= z.comp_poc + poc_tol) if above \
                    else (day["close"] >= z.comp_poc - poc_tol)
                a_end = -1
            else:
                beyond = highs[t0:] < L if above else lows[t0:] > L
                a_end = _run_end(beyond, n_acc)
                accepted = a_end >= 0

            if z.level_type == "VAH":
                target = z.comp_val
            elif z.level_type == "VAL":
                target = z.comp_vah
            else:
                target = z.comp_val if above else z.comp_vah
            down_target = target < L

            def reached(lo_arr, hi_arr):
                return bool((lo_arr <= target).any()) if down_target else bool((hi_arr >= target).any())

            reach = {0: reached(lows[t0:], highs[t0:])}
            for h in horizons:
                if h == 0:
                    continue
                fut = future.head(h)
                reach[h] = reach[0] or reached(fut["low"].to_numpy(), fut["high"].to_numpy())

            hi_after, lo_after = highs[t0:].max(), lows[t0:].min()
            mfe = (hi_after - L) if above else (L - lo_after)
            mae = (L - lo_after) if above else (hi_after - L)
            n_prev = touches[z.zone_id]
            row = {
                "date": date,
                "zone_id": z.zone_id,
                "composite_id": z.composite_id,
                "composite_start": z.composite_start,
                "composite_end": z.composite_end,
                "composite_days": z.composite_days,
                "level_type": z.level_type,
                "level_price": L,
                "zone_age_days": int((d["date"] > z.composite_end).iloc[:i + 1].sum()),
                "rolls_crossed": int(((roll_dates > np.datetime64(z.composite_end))
                                      & (roll_dates <= np.datetime64(date))).sum()),
                "touch_number": n_prev + 1,
                "zone_status": "virgin" if n_prev == 0 else "tested",
                # день, закрывший баланс: касание в нём отобрано самим пробоем
                "breakout_day": bool(z.available_from == date),
                "accepted_before": accepted_before[z.zone_id],
                "approach": "above" if above else "below",
                "gap_through": bool(gap_through),
                "open_minus_level": day["open"] - L,
                "first_touch_period": t0,
                "outcome": "acceptance" if accepted else "rejection",
                "acceptance_period": t0 + a_end if accepted else -1,
                "opposite_boundary": target,
                "comp_poc": z.comp_poc,
                "close_minus_poc": day["close"] - z.comp_poc,
                **{f"reached_opposite_d{h}": reach[h] for h in horizons},
                "mfe_ticks": round(mfe / tick),
                "mae_ticks": round(mae / tick),
                "mfe_atr": mfe / day["atr"] if day["atr"] else np.nan,
                "mae_atr": mae / day["atr"] if day["atr"] else np.nan,
                "multi_zone": len(idx),
            }
            rows.append(row)
            touches[z.zone_id] += 1
            if accepted:
                accepted_before[z.zone_id] = True

    ev = pd.DataFrame(rows)
    day_cols = ["date", "day_type", "profile_shape", "re_direction", "tpo_share_above_poc",
                "poc_position", "va_width_rel", "va_share_of_range", "single_prints",
                "tail_up_ticks", "tail_down_ticks", "poor_high", "poor_low", "value_migration",
                "range_vs_ib", "range_vs_avg", "close_position"]
    return ev.merge(d[day_cols], on="date", how="left")
