"""Поиск балансов и построение композитных профилей.

Алгоритм (параметры — config.balance):
  * идём по полным дням по порядку; короткие дни пропускаются (не входят
    в баланс и не прерывают его);
  * кандидат = список подряд идущих дней, композит = сумма их TPO;
  * день D добавляется, если ≥ va_overlap_min ширины VA дня D лежит внутри
    VA текущего композита И нет принятия за High/Low композита
    (≥ N подряд периодов целиком за экстремумом);
  * иначе кандидат закрывается. Если в нём ≥ min_days дней — это баланс.
    Если меньше — пробуем продолжить с последнего дня кандидата + D
    (чтобы не терять баланс, начавшийся со второго дня кандидата),
    иначе новый кандидат начинается с D;
  * зоны баланса доступны со дня, следующего за его последним днём
    (available_from = день закрытия), без look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .profile import Profile, merge_profiles, value_area


def va_overlap_share(val: int, vah: int, cval: int, cvah: int) -> float:
    """Доля VA дня [val, vah], лежащая внутри [cval, cvah] (в тиках, включительно)."""
    width = vah - val + 1
    inter = min(vah, cvah) - max(val, cval) + 1
    return max(inter, 0) / width


def accepted_beyond(lows: np.ndarray, highs: np.ndarray, top: int, bottom: int,
                    n: int, consecutive: bool) -> str:
    """'up' / 'down' / '' — есть ли ≥ n периодов целиком выше top или ниже bottom."""
    for side, mask in (("up", lows > top), ("down", highs < bottom)):
        if consecutive:
            run = best = 0
            for m in mask:
                run = run + 1 if m else 0
                best = max(best, run)
            if best >= n:
                return side
        elif mask.sum() >= n:
            return side
    return ""


@dataclass
class Candidate:
    dates: list = field(default_factory=list)
    profile: Profile | None = None
    poc: int = 0
    val: int = 0
    vah: int = 0

    def add(self, date, p: Profile, va_pct: float) -> None:
        self.dates.append(date)
        self.profile = p if self.profile is None else merge_profiles([self.profile, p])
        self.poc, self.val, self.vah = value_area(self.profile, va_pct)


def find_balances(bars: pd.DataFrame, daily: pd.DataFrame, profiles: dict,
                  cfg: dict, tick: float) -> tuple[pd.DataFrame, dict]:
    bcfg = cfg["balance"]
    acfg = cfg["interaction"]["acceptance"]
    va_pct = cfg["tpo"]["value_area_pct"]
    rules = set(bcfg["end_rules"])
    full = daily[~daily["short_day"]].sort_values("date").reset_index(drop=True)
    day_va = {r.date: (int(round(r.val / tick)), int(round(r.vah / tick))) for r in full.itertuples()}
    period_hl = {
        d: (np.rint(g.sort_values("period")["low"].to_numpy() / tick).astype(np.int64),
            np.rint(g.sort_values("period")["high"].to_numpy() / tick).astype(np.int64))
        for d, g in bars.groupby("date")
    }

    def fits(cand: Candidate, date) -> tuple[bool, str, float]:
        val, vah = day_va[date]
        ov = va_overlap_share(val, vah, cand.val, cand.vah)
        lows, highs = period_hl[date]
        acc = accepted_beyond(lows, highs, cand.profile.high, cand.profile.low,
                              acfg["periods_beyond"], acfg["consecutive"])
        if "acceptance_beyond_composite_extreme" in rules and acc:
            return False, f"acceptance_{acc}", ov
        if "va_overlap_below_min" in rules and ov < bcfg["va_overlap_min"]:
            return False, "va_overlap", ov
        return True, "", ov

    records, comp_profiles = [], {}

    def close(cand: Candidate, end_day, reason: str) -> None:
        if len(cand.dates) < bcfg["min_days"]:
            return
        cid = len(records)
        p = cand.profile
        records.append({
            "composite_id": cid,
            "start": cand.dates[0],
            "end": cand.dates[-1],
            "n_days": len(cand.dates),
            "poc": cand.poc * tick,
            "vah": cand.vah * tick,
            "val": cand.val * tick,
            "high": p.high * tick,
            "low": p.low * tick,
            "end_reason": reason,
            "available_from": end_day,     # первый день, когда зоны можно использовать
        })
        comp_profiles[cid] = p

    cand = Candidate()
    for date in full["date"]:
        p = profiles[date]
        if not cand.dates:
            cand.add(date, p, va_pct)
            continue
        ok, reason, _ = fits(cand, date)
        if ok:
            cand.add(date, p, va_pct)
            continue
        close(cand, date, reason)
        if len(cand.dates) < bcfg["min_days"] and len(cand.dates) > 1:
            # повтор с последнего дня кандидата
            last = cand.dates[-1]
            retry = Candidate()
            retry.add(last, profiles[last], va_pct)
            if fits(retry, date)[0]:
                retry.add(date, p, va_pct)
                cand = retry
                continue
        cand = Candidate()
        cand.add(date, p, va_pct)
    # незакрытый баланс в конце данных — формируется, зоны не используются
    if len(cand.dates) >= bcfg["min_days"]:
        close(cand, pd.NaT, "open")

    return pd.DataFrame(records), comp_profiles
