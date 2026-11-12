"""Внутридневная переоценка по периодам: локальный контекст (заметки «MP FP») + зоны → цели.

Строка = день × момент: после периода k (k = 0 — после A, 1 — после IB, … 11 — после L).
Признаки, известные на конец периода k:
  локально — тип открытия, принятие в A, ширина IB, RE за IB на этот момент,
             долгосрочные тейлы вчера, M вчера и гэп, «кто выигрывает»
             (сколько периодов обновили High дня минус сколько Low),
             что сделал последний период, где close периода относительно
             вчерашних VA / POC и сегодняшнего формирующегося POC;
  зоны     — close в зоне, расстояние до ближайшей зоны сверху / снизу и её тип,
             зона между ценой и вчерашним POC.
Цели (только ещё не достигнутые к концу периода k), до конца дня:
  POC / VAH / VAL / High / Low вчера, новое расширение вверх / вниз,
  ближайшая зона сверху / снизу.
Проверка: логистическая регрессия «локально» против «локально + зоны»,
обучение до split_date, оценка (AUC, log-loss) после; то же с
зонами, сдвинутыми случайно (placebo) — прирост должен быть выше, чем у них.
Плюс читаемые таблицы: вероятность вчерашнего POC по моменту × месту × контролю.

Запуск:  python -m scripts.period_targets [ES]
Результат: output/<INSTR>_period_targets.csv (панель), output/<INSTR>_period_targets_auc.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.day_context import active, hvn_class_table, shift_zones, zone_map
from scripts.hvn_va_stats import per_day
from scripts.primary_zones import zone_frame
from scripts.stage1_profiles import load_adjusted
from tpo.balance import find_balances
from tpo.data import ROOT, load_config
from tpo.day_context import LETTERS, grey_zones, open_location
from tpo.day_type import classify_days
from tpo.profile import build_profile, poc_index

TARGETS = ["y_poc", "y_vah", "y_val", "y_high", "y_low", "ext_up", "ext_dn", "zone_up", "zone_dn"]
T_NAME = {"y_poc": "POC вчера", "y_vah": "VAH вчера", "y_val": "VAL вчера", "y_high": "снять High вчера",
          "y_low": "снять Low вчера", "ext_up": "новый High дня", "ext_dn": "новый Low дня",
          "zone_up": "зона сверху", "zone_dn": "зона снизу"}
LOCAL = ["k", "open_cat", "a_accept", "ib_w", "re_up", "re_dn", "y_tail_up_lt", "y_tail_dn_lt", "y_m_poc", "gap_up",
         "control", "last", "px_vs_yva", "px_above_ypoc", "px_above_dpoc", "dist_ypoc_rel"]
ZONE = ["in_zone", "zup_dist", "zdn_dist", "zup_type", "zdn_type", "obstacle_ypoc"]


def zone_feats(a, px, ypoc, tol, cap=60.0):
    r = {}
    r["in_zone"] = bool(((a.lo - tol <= px) & (a.hi + tol >= px)).any())
    up = a[a.lo - tol > px]
    dn = a[a.hi + tol < px]
    if len(up):
        z = up.loc[up.lo.idxmin()]
        r["zup_dist"], r["zup_type"], r["zup_lo"], r["zup_hi"] = min(z.lo - px, cap), z.type, z.lo, z.hi
    else:
        r["zup_dist"], r["zup_type"], r["zup_lo"], r["zup_hi"] = cap, "нет", np.nan, np.nan
    if len(dn):
        z = dn.loc[dn.hi.idxmax()]
        r["zdn_dist"], r["zdn_type"], r["zdn_lo"], r["zdn_hi"] = min(px - z.hi, cap), z.type, z.lo, z.hi
    else:
        r["zdn_dist"], r["zdn_type"], r["zdn_lo"], r["zdn_hi"] = cap, "нет", np.nan, np.nan
    lo, hi = sorted((px, ypoc))
    r["obstacle_ypoc"] = bool(((a.lo > lo + tol) & (a.hi < hi - tol)).any())
    return r


def build_panel(bars, daily, cfg, tick, zmap, cls_tab, local_only=False):
    cc = cfg["day_context"]
    share = cfg["wicks"]["class_rejection_share"]
    tol = cc["zone_tolerance_ticks"] * tick
    d = daily.sort_values("date").reset_index(drop=True)
    by_day = {k: g.sort_values("period").reset_index(drop=True) for k, g in bars.groupby("date")}
    ib_avg = (d.ib_high - d.ib_low).where(~d.short_day).rolling(20, min_periods=10).mean().shift(1)
    mn = cfg["tpo"]["tail_min_rows"] * cfg["tpo"]["price_row_ticks"]
    ext_share = cfg["day_type"]["min_extension_share_of_ib"]
    rows = []
    for i in range(2, len(d)):
        y, t = d.iloc[i - 1], d.iloc[i]
        if t.short_day or y.short_day or np.isnan(ib_avg[i]):
            continue
        g = by_day[t.date]
        if len(g) != cfg["data"]["expected_periods_per_day"]:
            continue
        a = active(zmap, t.date, cls_tab, share)
        M = by_day[y.date].iloc[-1]
        cpos = (y.close - y.low) / (y.high - y.low) if y.high > y.low else 0.5
        base = {"date": t.date,
                "open_cat": {"внутри VA": "balance", "VAH–High": "dis_in_range",
                             "Low–VAL": "dis_in_range"}.get(open_location(t.open, y), "full_dis"),
                "y_tail_up_lt": bool(y.tail_up_ticks >= mn and cpos <= 0.5),
                "y_tail_dn_lt": bool(y.tail_down_ticks >= mn and cpos >= 0.5),
                "y_m_poc": bool(M.close > y.poc), "gap_up": bool(t.open > y.close)}
        H, L, C = g.high.to_numpy(), g.low.to_numpy(), g.close.to_numpy()
        lows_t = np.rint(L / tick).astype(np.int64)
        highs_t = np.rint(H / tick).astype(np.int64)
        A_close = C[0]
        ibh, ibl = H[:2].max(), L[:2].min()
        ext = ext_share * (ibh - ibl)
        n = len(g)
        for k in range(0, n - 1):
            hi_k, lo_k, px = H[:k + 1].max(), L[:k + 1].min(), C[k]
            r = dict(base)
            r["k"] = k
            r["a_accept"] = bool(y.val <= A_close <= y.vah)
            r["ib_w"] = ((ibh - ibl) / ib_avg[i]) if k >= 1 else np.nan
            r["re_up"] = bool(k >= 2 and H[2:k + 1].max() > ibh + ext)
            r["re_dn"] = bool(k >= 2 and L[2:k + 1].min() < ibl - ext)
            # кто выигрывает: периоды, обновившие High / Low дня
            nh = sum(H[j] > H[:j].max() for j in range(1, k + 1))
            nl = sum(L[j] < L[:j].min() for j in range(1, k + 1))
            r["control"] = int(nh - nl)
            if k == 0:
                r["last"] = "A"
            else:
                up_, dn_ = H[k] > H[:k].max(), L[k] < L[:k].min()
                r["last"] = "оба" if up_ and dn_ else "новый High" if up_ else "новый Low" if dn_ else "внутри"
            r["px_vs_yva"] = "выше" if px > y.vah else ("ниже" if px < y.val else "внутри")
            r["px_above_ypoc"] = bool(px > y.poc)
            pf = build_profile(lows_t[:k + 1], highs_t[:k + 1], 1)
            dpoc = (pf.low + poc_index(pf)) * tick
            r["px_above_dpoc"] = bool(px > dpoc)
            r["dist_ypoc_rel"] = (px - y.poc) / max(y.high - y.low, tick)
            zf = zone_feats(a, px, y.poc, tol)
            r.update({k2: v for k2, v in zf.items() if not k2.endswith(("_lo", "_hi"))})
            # цели: ещё не достигнутые к концу периода k → достигнуты ли позже
            Hl, Ll = (H[k + 1:].max(), L[k + 1:].min())
            def tgt(name, done, later):
                r[name] = np.nan if done else float(later)
            tgt("y_poc", lo_k <= y.poc <= hi_k, Ll <= y.poc <= Hl or (Ll <= y.poc and Hl >= y.poc))
            tgt("y_vah", hi_k >= y.vah, Hl >= y.vah)
            tgt("y_val", lo_k <= y.val, Ll <= y.val)
            tgt("y_high", hi_k > y.high, Hl > y.high)
            tgt("y_low", lo_k < y.low, Ll < y.low)
            r["ext_up"] = float(Hl > hi_k)
            r["ext_dn"] = float(Ll < lo_k)
            r["zone_up"] = np.nan if np.isnan(zf["zup_lo"]) else float(Hl >= zf["zup_lo"] - tol)
            r["zone_dn"] = np.nan if np.isnan(zf["zdn_hi"]) else float(Ll <= zf["zdn_hi"] + tol)
            rows.append(r)
    return pd.DataFrame(rows)


def design(df, cols):
    X = df[cols].copy()
    for c in X.columns:
        if X[c].dtype == bool:
            X[c] = X[c].astype(float)
    X["ib_w"] = X["ib_w"].fillna(1.0) if "ib_w" in X else None
    X = pd.get_dummies(X, columns=[c for c in X.columns if X[c].dtype == object or c == "k"], dtype=float)
    return X.fillna(0.0)


def evaluate(df, split, feats):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import log_loss, roc_auc_score
    out = {}
    X_all = design(df, feats)
    for tg in TARGETS:
        m = df[tg].notna()
        X, yv = X_all[m], df.loc[m, tg].astype(int)
        tr, te = (df.date[m] < split).to_numpy(), (df.date[m] >= split).to_numpy()
        if yv[tr].nunique() < 2 or yv[te].nunique() < 2:
            continue
        clf = LogisticRegression(max_iter=2000, C=1.0).fit(X[tr], yv[tr])
        p = clf.predict_proba(X[te])[:, 1]
        out[tg] = {"auc": roc_auc_score(yv[te], p), "logloss": log_loss(yv[te], p), "n_test": int(te.sum()),
                   "base_rate": yv[te].mean()}
    return out


def main(instrument: str = "ES") -> None:
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
    share = cfg["wicks"]["class_rejection_share"]
    cls_tab = hvn_class_table(bars, prim, cfg["wicks"]["min_wick_ticks"] * tick, share)
    zmap = zone_map(comps, prim, grey, cls_tab, share)
    split = pd.Timestamp(cc["split_date"])

    df = build_panel(bars, daily, cfg, tick, zmap, cls_tab)
    df.to_csv(out / f"{instrument}_period_targets.csv", index=False)
    print(f"{instrument}: дней {df.date.nunique()}, строк (день × момент) {len(df)}; "
          f"обучение до {split:%Y-%m-%d}, проверка после")

    # читаемые таблицы
    print("\nВчерашний POC до конца дня (если ещё не взят): момент × где цена × кто выигрывает")
    for k in (0, 1, 3, 5, 8):
        s = df[(df.k == k) & df.y_poc.notna()].copy()
        s["ctrl"] = np.sign(s.control).map({1: "покупатель", -1: "продавец", 0: "поровну"})
        s["dir"] = np.where(s.px_above_ypoc, "цена выше POC", "цена ниже POC")
        # «за нас» — контроль в сторону POC
        s["toward"] = np.where((s.px_above_ypoc & (s.control < 0)) | (~s.px_above_ypoc & (s.control > 0)),
                               "контроль к POC", np.where(s.control == 0, "поровну", "контроль от POC"))
        tab = s.groupby(["px_vs_yva", "toward"]).y_poc.agg(["mean", "size"])
        tab = tab[tab["size"] >= 20]
        print(f"\n  после {LETTERS[k]}{' (IB)' if k == 1 else ''}:")
        for (loc, tw), r in tab.iterrows():
            print(f"    цена {loc:7s} VA · {tw:16s}  POC вчера {r['mean']:5.1%}  (n={int(r['size'])})")

    print("\nНовый High / Low дня до конца дня: момент × кто выигрывает × последний период")
    for k in (1, 3, 5):
        s = df[df.k == k].copy()
        s["ctrl"] = np.sign(s.control).map({1: "покупатель", -1: "продавец", 0: "поровну"})
        tab = s.groupby(["ctrl", "last"])[["ext_up", "ext_dn"]].agg(["mean"]).join(s.groupby(["ctrl", "last"]).size().rename("n"))
        tab = tab[tab.n >= 20]
        print(f"\n  после {LETTERS[k]}:")
        for (c, l), r in tab.iterrows():
            print(f"    {c:10s} · последний {l:11s}  новый High {r[('ext_up', 'mean')]:5.1%}  "
                  f"новый Low {r[('ext_dn', 'mean')]:5.1%}  (n={int(r['n'])})")

    # модели: локально vs локально + зоны vs локально + фальш-зоны
    print("\nДобавляют ли зоны что-то к локальному контексту (AUC на 2025–2026, выше — лучше):")
    loc = evaluate(df, split, LOCAL)
    full = evaluate(df, split, LOCAL + ZONE)
    rng = np.random.default_rng(cfg["stats"]["placebo"]["seed"])
    plac = []
    for _ in range(5):
        pz = shift_zones(zmap, rng, tick)
        pdf = build_panel(bars, daily, cfg, tick, pz, cls_tab)
        plac.append(evaluate(pdf, split, LOCAL + ZONE))
    rows = []
    print(f"  {'цель':18s} {'база':>6s} {'локально':>9s} {'+ зоны':>8s} {'+ фальш-зоны (5)':>18s}   n")
    for tg in TARGETS:
        if tg not in loc or tg not in full:
            continue
        pa = [p[tg]["auc"] for p in plac if tg in p]
        print(f"  {T_NAME[tg]:18s} {loc[tg]['base_rate']:6.1%} {loc[tg]['auc']:9.3f} {full[tg]['auc']:8.3f} "
              f"{np.mean(pa):9.3f} ({min(pa):.3f}–{max(pa):.3f})   {loc[tg]['n_test']}")
        rows.append({"target": T_NAME[tg], "base_rate": loc[tg]["base_rate"], "auc_local": loc[tg]["auc"],
                     "auc_local_zones": full[tg]["auc"], "auc_local_placebo_mean": np.mean(pa),
                     "auc_local_placebo_min": min(pa), "auc_local_placebo_max": max(pa),
                     "logloss_local": loc[tg]["logloss"], "logloss_local_zones": full[tg]["logloss"]})
    pd.DataFrame(rows).to_csv(out / f"{instrument}_period_targets_auc.csv", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
