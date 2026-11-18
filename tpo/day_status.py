"""Статус дня по периодам: только сегодняшний RTH-день, вчерашний профиль и зоны 1.8 на сегодня.

Строка панели = день × момент (конец периода k: 0 — A, 1 — B (IB), … 11 — L). Признаки — только по барам дня до конца
периода k включительно, open, вчерашнему RTH-профилю и зонам 1.8 к открытию дня (ZoneSnap). Цели — уровни, ещё не
взятые к концу k: достигнуты ли до конца RTH. Цены — графика (сырые RTH 30m, как в MotiveWave).

nPOC (голый POC) — POC прошлого RTH-дня того же контракта (после последнего ролла), который ни один следующий RTH-день
не задел (low ≤ POC ≤ high), до вчерашнего дня включительно; ищется в 20 последних днях.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tpo.profile import build_profile, poc_index
from tpo.zones18 import open_loc

LETTERS = "ABCDEFGHIJKLM"
COLOR = {"CORE": "синяя", "HVN_S": "зелёная", "HVN_R": "красная", "GREY": "серая"}


def naked_pocs(days: pd.DataFrame, lookback: int = 20) -> dict:
    """date → массив голых POC к открытию дня (того же контракта)."""
    d = days[~days.short_day].reset_index(drop=True)
    out = {}
    contract_start = 0
    for i in range(len(d)):
        if d.roll_day[i]:
            contract_start = i
        lo = max(contract_start, i - lookback)
        res = []
        for j in range(lo, i - 1 + 1):
            if j >= i:
                break
            p = d.poc[j]
            later = d.iloc[j + 1:i]
            if not ((later.low <= p) & (later.high >= p)).any():
                res.append(p)
        out[d.date[i]] = np.array(res)
    return out


def _zone_at(z: pd.DataFrame, px: float, tol: float):
    m = (z.lo - tol <= px) & (z.hi + tol >= px)
    return z[m].iloc[0] if m.any() else None


def panel_day(day, g: pd.DataFrame, z: pd.DataFrame, npocs: np.ndarray, tick: float, avg_rng: float,
              ib_avg: float, y_shape: str, tol_ticks: int = 2) -> list[dict]:
    O = g.open.iloc[0]
    H, L, C = g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy()
    n = len(g)
    tol = tol_ticks * tick
    lows_t = np.rint(L / tick).astype(np.int64)
    highs_t = np.rint(H / tick).astype(np.int64)
    zc = z[z.kind != "GREY"]
    zb = z[z.kind == "CORE"]
    z_open = _zone_at(z, O, 0.0)
    ibh, ibl = H[:2].max(), L[:2].min()
    rows = []
    for k in range(n - 1):
        hi_k, lo_k, px = H[:k + 1].max(), L[:k + 1].min(), C[k]
        pf = build_profile(lows_t[:k + 1], highs_t[:k + 1], 1)
        ip = poc_index(pf)
        dpoc = (pf.low + ip) * tick
        rng = max(hi_k - lo_k, tick)
        pos = (dpoc - lo_k) / rng
        c = pf.counts
        top1 = 0
        while top1 < len(c) and c[len(c) - 1 - top1] == 1:
            top1 += 1
        bot1 = 0
        while bot1 < len(c) and c[bot1] == 1:
            bot1 += 1
        nh = sum(H[j] > H[:j].max() for j in range(1, k + 1))
        nl = sum(L[j] < L[:j].min() for j in range(1, k + 1))
        if k == 0:
            last = "A"
        else:
            u, d_ = H[k] > H[:k].max(), L[k] < L[:k].min()
            last = "оба" if u and d_ else "новый High" if u else "новый Low" if d_ else "внутри"
        re_up = k >= 2 and H[2:k + 1].max() > ibh
        re_dn = k >= 2 and L[2:k + 1].min() < ibl
        zp = _zone_at(z, px, tol)
        up = zc[zc.lo - tol > px]
        dn = zc[zc.hi + tol < px]
        zu = up.loc[up.lo.idxmin()] if len(up) else None
        zd = dn.loc[dn.hi.idxmax()] if len(dn) else None
        bu = zb[zb.lo - tol > px]
        bd = zb[zb.hi + tol < px]
        bu = bu.loc[bu.lo.idxmin()] if len(bu) else None
        bd = bd.loc[bd.hi.idxmax()] if len(bd) else None
        npu = npocs[npocs > hi_k] if len(npocs) else npocs
        npd = npocs[npocs < lo_k] if len(npocs) else npocs
        Hl, Ll = H[k + 1:].max(), L[k + 1:].min()
        r = {
            "date": day.date, "k": k, "moment": LETTERS[k] if k else "A",
            # локально
            "open_loc": open_loc(O, day), "gap": "вверх" if O > day.y_close else "вниз", "y_shape": y_shape,
            "a_accept": "принят" if day.y_val <= C[0] <= day.y_vah else "отвергнут",
            "ib_rel": "—" if k < 1 else ("узкий" if (ibh - ibl) / ib_avg < 0.75 else "широкий" if (ibh - ibl) / ib_avg > 1.25 else "средний"),
            "re": "—" if k < 2 else ("обе" if re_up and re_dn else "вверх" if re_up else "вниз" if re_dn else "нет"),
            "control": int(np.clip(nh - nl, -3, 3)), "last": last,
            "px_vs_yva": "выше VA" if px > day.y_vah else "ниже VA" if px < day.y_val else "в VA",
            "px_vs_ypoc": "выше" if px > day.y_poc else "ниже",
            "px_vs_dpoc": "выше" if px > dpoc else "ниже" if px < dpoc else "на",
            "pshape": "—" if k < 2 else ("P" if pos >= 2 / 3 else "b" if pos <= 1 / 3 else "D"),
            "tail_up": "—" if k < 1 else ("есть" if top1 >= 8 else "нет"),        # single prints сверху ≥ 2 п.
            "tail_dn": "—" if k < 1 else ("есть" if bot1 >= 8 else "нет"),
            "px_in_range": float(np.clip((px - lo_k) / rng, 0, 1)),
            "rng_rel": rng / avg_rng,
            # зоны 1.8
            "zone_px": COLOR[zp.kind] if zp is not None else "нет",
            "zone_open": COLOR[z_open.kind] if z_open is not None else "нет",
            "zone_up": COLOR[zu.kind] if zu is not None else "нет",
            "zone_dn": COLOR[zd.kind] if zd is not None else "нет",
            "zu_dist": min((zu.lo - px) / avg_rng, 2.0) if zu is not None else 2.0,
            "zd_dist": min((px - zd.hi) / avg_rng, 2.0) if zd is not None else 2.0,
        }
        r["ypoc_dist"] = (day.y_poc - px) / avg_rng

        def tgt(name, done, later):
            r[name] = np.nan if done else float(later)
        tgt("y_poc", lo_k <= day.y_poc <= hi_k, Ll <= day.y_poc <= Hl)
        tgt("y_vah", hi_k >= day.y_vah, Hl >= day.y_vah)
        tgt("y_val", lo_k <= day.y_val, Ll <= day.y_val)
        tgt("y_high", hi_k > day.y_high, Hl > day.y_high)
        tgt("y_low", lo_k < day.y_low, Ll < day.y_low)
        r["ext_up"], r["ext_dn"] = float(Hl > hi_k), float(Ll < lo_k)
        r["npoc_up_px"] = npu.min() if len(npu) else np.nan
        r["npoc_dn_px"] = npd.max() if len(npd) else np.nan
        r["npoc_up"] = float(Hl >= r["npoc_up_px"]) if len(npu) else np.nan
        r["npoc_dn"] = float(Ll <= r["npoc_dn_px"]) if len(npd) else np.nan
        r["npoc_up_dist"] = (r["npoc_up_px"] - px) / avg_rng if len(npu) else np.nan
        r["npoc_dn_dist"] = (px - r["npoc_dn_px"]) / avg_rng if len(npd) else np.nan
        r["zu_px"] = zu.lo if zu is not None else np.nan
        r["zd_px"] = zd.hi if zd is not None else np.nan
        r["zone_up_hit"] = float(Hl >= zu.lo - tol) if zu is not None else np.nan
        r["zone_dn_hit"] = float(Ll <= zd.hi + tol) if zd is not None else np.nan
        r["blue_up_px"] = bu.lo if bu is not None else np.nan
        r["blue_dn_px"] = bd.hi if bd is not None else np.nan
        r["blue_up"] = float(Hl >= bu.lo - tol) if bu is not None else np.nan
        r["blue_dn"] = float(Ll <= bd.hi + tol) if bd is not None else np.nan
        r["blue_up_dist"] = min((bu.lo - px) / avg_rng, 3.0) if bu is not None else np.nan
        r["blue_dn_dist"] = min((px - bd.hi) / avg_rng, 3.0) if bd is not None else np.nan
        # открылись в зоне: закрытие дня в ней
        if z_open is not None:
            r["open_zone_lo"], r["open_zone_hi"] = z_open.lo, z_open.hi
            r["close_in_open_zone"] = float(z_open.lo - tol <= C[-1] <= z_open.hi + tol)
        rows.append(r)
    return rows
