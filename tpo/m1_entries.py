"""Вход на минутке от уровня 30m: механическое приближение «зона → прокол → возврат → вход, стоп за проколом».

Минутные RTH-бары (сырые, цены графика, совпадают с 30m-рядом). Для зоны [lo, hi] (зона 1.8 или уровень вчера ± 0.5 п.):
  * сторона — по close предыдущей минуты: выше зоны → ждём лонг от поддержки, ниже → шорт от сопротивления;
  * касание — минута дотянулась до зоны; дальше копится прокол (минимум low для лонга);
  * close минуты за дальним краем — зона принята, попытка снимается, сторона пересчитывается;
  * вход — первая минута, закрывшаяся обратно за ближним краем (close > hi для лонга) не позже `wait` минут после касания;
    стоп — экстремум прокола ∓ `buf`; риск в [min_risk, max_risk], иначе попытка пропускается;
  * сопровождение — по минутам после входа: стоп раньше цели в той же минуте; MFE в R до стопа (минута стопа не
    считается); не решилось — выход по close RTH.
Одна сделка на зону за день (первая). Результат для цели T: T, если MFE до стопа ≥ T; −1, если стоп; иначе close в R.
"""
from __future__ import annotations

import numpy as np


def trades_for_day(O, H, L, C, V, zones, start: int = 0, wait: int = 10, buf: float = 0.5,
                   min_risk: float = 0.75, max_risk: float = 5.0) -> list[dict]:
    n = len(C)
    out = []
    vmed = np.array([np.median(V[max(0, i - 30):i]) if i >= 5 else np.nan for i in range(n)])
    for zi, (lo, hi) in enumerate(zones):
        if lo - 0.25 > H.max() or hi + 0.25 < L.min():
            continue
        i = max(start, 1)
        done = False
        while i < n and not done:
            pc = C[i - 1]
            if lo <= pc <= hi:
                i += 1
                continue
            up = pc > hi
            if not ((L[i] <= hi) if up else (H[i] >= lo)):
                i += 1
                continue
            t0 = i
            ext = L[i] if up else H[i]
            pv = V[i]
            j = i
            entry = None
            while j < n and j - t0 <= wait:
                ext = min(ext, L[j]) if up else max(ext, H[j])
                pv = max(pv, V[j])
                if (C[j] < lo) if up else (C[j] > hi):          # принята — попытка снимается
                    break
                if j > t0 and ((C[j] > hi) if up else (C[j] < lo)):
                    entry = j
                    break
                if j == t0 and ((C[j] > hi) if up else (C[j] < lo)):   # хвост в той же минуте и close обратно
                    entry = j
                    break
                j += 1
            if entry is None:
                i = j + 1
                continue
            e = C[entry]
            stop = ext - buf if up else ext + buf
            risk = abs(e - stop)
            if not (min_risk <= risk <= max_risk):
                i = entry + 1
                continue
            mfe, stopped, close_r = 0.0, False, 0.0
            for k in range(entry + 1, n):
                if (L[k] <= stop) if up else (H[k] >= stop):
                    stopped = True
                    break
                mfe = max(mfe, ((H[k] - e) if up else (e - L[k])) / risk)
            if not stopped:
                close_r = ((C[-1] - e) if up else (e - C[-1])) / risk
            out.append({"zi": zi, "minute": entry, "touch_minute": t0, "side": "лонг" if up else "шорт", "entry": e,
                        "risk": risk, "mfe": mfe, "stopped": stopped, "close_r": close_r,
                        "probe_vol": pv / vmed[t0] if vmed[t0] and vmed[t0] > 0 else np.nan})
            done = True
    return out


def result_r(df, target: float) -> np.ndarray:
    """R сделки при цели target."""
    return np.where(df.mfe >= target, target, np.where(df.stopped, -1.0, np.minimum(df.close_r, target)))
