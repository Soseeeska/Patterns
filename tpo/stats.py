"""Сводная статистика событий и сравнение с фальш-зонами (placebo).

Фальш-зоны: те же композиты, но все три уровня (VAH/VAL/POC) сдвинуты вместе
на случайное ±U(lo, hi) × ширина VA композита. Структура (ширина VA, дистанция
до противоположной границы, время появления) сохраняется, «настоящих»
уровней нет. Эффект реальных зон = выход их цифр за разброс фальш-зон.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .events import build_events

TOUCH_ORDER = ["1 (virgin)", "2-3", "4+"]


def touch_bucket(n: int) -> str:
    return "1 (virgin)" if n == 1 else ("2-3" if n <= 3 else "4+")


def add_oriented(ev: pd.DataFrame) -> pd.DataFrame:
    """Метрики профиля, развёрнутые относительно стороны подхода:
    значения ближе к 1 — «в сторону, откуда пришла цена» (= отбой от уровня)."""
    ev = ev.copy()
    up = ev["approach"] == "above"
    ev["touch_bucket"] = ev["touch_number"].map(touch_bucket)
    ev["close_pos_dir"] = np.where(up, ev["close_position"], 1 - ev["close_position"])
    ev["poc_pos_dir"] = np.where(up, ev["poc_position"], 1 - ev["poc_position"])
    ev["tpo_share_dir"] = np.where(up, ev["tpo_share_above_poc"], 1 - ev["tpo_share_above_poc"])
    ev["tail_level_side"] = np.where(up, ev["tail_down_ticks"], ev["tail_up_ticks"])
    ev["poor_extreme_level_side"] = np.where(up, ev["poor_low"], ev["poor_high"])
    vm = ev["value_migration"].fillna("")
    vm_up = vm.isin(["higher", "overlap_higher"])
    vm_dn = vm.isin(["lower", "overlap_lower"])
    ev["value_dir"] = np.select(
        [(up & vm_up) | (~up & vm_dn), (up & vm_dn) | (~up & vm_up)],
        ["от уровня", "за уровень"], "нейтрально")
    return ev


def summarize(ev: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = ev.groupby(by, observed=True)
    out = pd.DataFrame({
        "n": g.size(),
        "acceptance": g["outcome"].apply(lambda s: (s == "acceptance").mean()),
        "reached_d0": g["reached_opposite_d0"].mean(),
        "reached_d1": g["reached_opposite_d1"].mean(),
        "reached_d3": g["reached_opposite_d3"].mean(),
        "mfe_atr": g["mfe_atr"].median(),
        "mae_atr": g["mae_atr"].median(),
    })
    return out


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def placebo_comps(comps: pd.DataFrame, rng: np.random.Generator, cfg: dict, tick: float) -> pd.DataFrame:
    pc = cfg["stats"]["placebo"]
    lo, hi = pc["shift_va_widths"]
    c = comps.copy()
    width = np.maximum(c["vah"] - c["val"], pc["min_va_width_ticks"] * tick)
    shift = rng.uniform(lo, hi, len(c)) * width * rng.choice([-1, 1], len(c))
    shift = np.round(shift / tick) * tick
    for col in ["vah", "val", "poc", "high", "low"]:
        c[col] = c[col] + shift
    return c


def run_placebo(bars, days, comps, rolls, cfg, tick) -> list[pd.DataFrame]:
    pc = cfg["stats"]["placebo"]
    rng = np.random.default_rng(pc["seed"])
    runs = []
    for k in range(pc["runs"]):
        ev = build_events(bars, days, placebo_comps(comps, rng, cfg, tick), rolls, cfg, tick)
        ev["run"] = k
        runs.append(ev)
    return runs


def compare(real: pd.DataFrame, placebo: list[pd.DataFrame], by: list[str],
            metrics: list[str], min_n: int) -> pd.DataFrame:
    """Таблица: реальное значение, 95% Wilson CI (для долей), среднее и
    2.5–97.5% диапазон по прогонам фальш-зон, вердикт."""
    r = summarize(real, by)
    ps = [summarize(p, by) for p in placebo]
    rows = []
    for key in r.index:
        row = {"key": key if isinstance(key, tuple) else (key,), "n": int(r.at[key, "n"])}
        for m in metrics:
            vals = np.array([p.at[key, m] for p in ps if key in p.index], dtype=float)
            real_v = float(r.at[key, m])
            row[m] = real_v
            if m in ("acceptance", "reached_d0", "reached_d1", "reached_d3"):
                row[m + "_ci"] = wilson(real_v, row["n"])
            if len(vals):
                plo, phi = np.nanpercentile(vals, [2.5, 97.5])
                row[m + "_placebo"] = float(np.nanmean(vals))
                row[m + "_placebo_range"] = (float(plo), float(phi))
                row[m + "_verdict"] = ("выше фона" if real_v > phi else
                                       "ниже фона" if real_v < plo else "как фон")
            else:
                row[m + "_placebo"] = np.nan
                row[m + "_placebo_range"] = (np.nan, np.nan)
                row[m + "_verdict"] = "нет фона"
        row["placebo_n"] = float(np.mean([p.at[key, "n"] for p in ps if key in p.index])) if ps else 0
        row["small"] = row["n"] < min_n
        rows.append(row)
    return pd.DataFrame(rows)


def distribution(ev: pd.DataFrame, col: str, by: str | None = None) -> pd.DataFrame:
    if by is None:
        return ev[col].value_counts(normalize=True).rename("share").to_frame()
    return ev.groupby(by, observed=True)[col].value_counts(normalize=True).unstack(fill_value=0)


def placebo_distribution(placebo: list[pd.DataFrame], col: str) -> pd.DataFrame:
    shares = pd.concat([p[col].value_counts(normalize=True) for p in placebo], axis=1).fillna(0)
    return pd.DataFrame({"mean": shares.mean(axis=1),
                         "lo": shares.quantile(0.025, axis=1),
                         "hi": shares.quantile(0.975, axis=1)})
