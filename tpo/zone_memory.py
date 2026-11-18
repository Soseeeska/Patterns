"""Память зоны: сколько раз цена её отторгала и принимала — от появления зоны до открытия RTH дня.

По всем 30m-свечам ряда (ETH + RTH; до 2023-07 только RTH), цены графика. Свеча j, сторона — по close свечи j − 1:
  * сверху (close выше зоны): касание — low ≤ hi; отторжение — касание и close > hi (зона сработала поддержкой),
    иначе принятие (close в зоне или ниже);
  * снизу (close ниже зоны): касание — high ≥ lo; отторжение — касание и close < lo (сопротивлением), иначе принятие.
Close внутри зоны — стороны нет, свеча не считается. Зона живёт с начала сессии дня появления (17:00 CT накануне).

Визиты (сильная память): визит — касание с той же стороны, перед которым 4 свечи (2 часа) касаний с этой стороны не было;
  * сильное отторжение — за 4 свечи после касания цена ушла от края на away_pts и ни один close (с касания) не за дальним
    краем;
  * принятие — за касание и 4 свечи после — два close подряд за дальним краем.
Визит, чьи 4 свечи заходят за открытие дня, не считается (не подглядываем).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def load_series(path) -> pd.DataFrame:
    b = pd.read_csv(path, header=None, names=["t", "open", "high", "low", "close"])
    return b.sort_values("t").reset_index(drop=True)


def ms(ts: pd.Timestamp, tz: str) -> int:
    return int(ts.tz_localize(tz).value // 1_000_000)


def _win(x: np.ndarray, w: int, fn, fill) -> np.ndarray:
    """fn по окну x[j+1 … j+w] для каждого j (за концом — fill)."""
    pad = np.concatenate([x[1:], np.full(w, fill)])
    return fn(np.lib.stride_tricks.sliding_window_view(pad, w), axis=1)[:len(x)]


def history(b: pd.DataFrame, lo: float, hi: float, start_ms: int, cut_ms: np.ndarray, away_pts: float = 8.0,
            w: int = 4) -> dict:
    """Счётчики для зоны [lo, hi] по свечам с start_ms до каждого cut_ms (не включая): массивы по cut_ms."""
    t = b.t.to_numpy()
    i0 = int(np.searchsorted(t, start_ms))
    H, L, C = b.high.to_numpy()[i0:], b.low.to_numpy()[i0:], b.close.to_numpy()[i0:]
    pc = np.concatenate([[np.nan], C[:-1]])
    above, below = pc > hi, pc < lo
    ta = above & (L <= hi)
    tb = below & (H >= lo)
    ra = ta & (C > hi)
    rb = tb & (C < lo)
    idx = np.searchsorted(t[i0:], cut_ms)                 # свечей до cut
    out = {}
    for name, arr in (("touch_sup", ta), ("rej_sup", ra), ("touch_res", tb), ("rej_res", rb)):
        cs = np.concatenate([[0], np.cumsum(arr)])
        out[name] = cs[idx]
    # визиты
    n = len(C)
    if n == 0:
        for name in ("visit_sup", "srej_sup", "acc_sup", "visit_res", "srej_res", "acc_res"):
            out[name] = np.zeros(len(idx), dtype=int)
        return out
    hmax = _win(H, w, np.max, -np.inf)
    lmin = _win(L, w, np.min, np.inf)
    cmin = np.minimum(C, _win(C, w, np.min, np.inf))
    cmax = np.maximum(C, _win(C, w, np.max, -np.inf))
    below_lo = (C < lo).astype(int)
    above_hi = (C > hi).astype(int)
    two_dn = np.concatenate([below_lo[:-1] & below_lo[1:], [0]])        # close j и j+1 за нижним краем
    two_up = np.concatenate([above_hi[:-1] & above_hi[1:], [0]])
    acc_dn = np.maximum(two_dn, _win(two_dn, w - 1, np.max, 0)) > 0
    acc_up = np.maximum(two_up, _win(two_up, w - 1, np.max, 0)) > 0
    prev_a = np.concatenate([np.zeros(w, bool), ta])
    prev_b = np.concatenate([np.zeros(w, bool), tb])
    va = ta & ~np.lib.stride_tricks.sliding_window_view(prev_a[:-1], w).any(axis=1)[:n]
    vb = tb & ~np.lib.stride_tricks.sliding_window_view(prev_b[:-1], w).any(axis=1)[:n]
    sra = va & (hmax >= hi + away_pts) & (cmin >= lo)
    srb = vb & (lmin <= lo - away_pts) & (cmax <= hi)
    aca = va & acc_dn
    acb = vb & acc_up
    idx_w = np.clip(idx - w, 0, None)                     # визит должен закончиться до открытия дня
    for name, arr in (("visit_sup", va), ("srej_sup", sra), ("acc_sup", aca),
                      ("visit_res", vb), ("srej_res", srb), ("acc_res", acb)):
        cs = np.concatenate([[0], np.cumsum(arr)])
        out[name] = cs[idx_w]
    return out
