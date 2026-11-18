"""Статус дня по периодам: вероятности целей до конца RTH из сегодняшнего дня, вчерашнего профиля и зон 1.8.

Панель — tpo/day_status.py (день × конец периода A…L). Модель — среднее бустинга и логистической регрессии (Blend), обучение до
split_date, проверка на 2025+: AUC и калибровка (обещанная вероятность против сбывшейся). Зоны — отдельной проверкой:
модель «локально» против «локально + зоны 1.8» против «локально + зоны сдвинутой карты» (±1.5–6 п., 3 прогона).
Расстояние до цели (в средних дневных диапазонах) есть во всех моделях — это геометрия, а не зона.

Вход: output/z18/snap_*.csv (ZoneSnap). Запуск:
    python -m scripts.day_status                   — панель, проверка, отчёт
    python -m scripts.day_status card 2026-11-06   — карточка статуса дня по периодам (модель учится на днях до 2025)
Результат: output/z18/day_status.csv (панель), отчёт в stdout.
"""
from __future__ import annotations

import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

from scripts.zones18_mp import OUT
from tpo.data import load_bars, load_config
from tpo.day_status import naked_pocs, panel_day
from tpo.day_type import classify_days
from tpo.profile import daily_profiles
from tpo.zones18 import day_table, load_snaps

LOCAL = ["k", "open_loc", "gap", "y_shape", "a_accept", "ib_rel", "re", "control", "last", "px_vs_yva", "px_vs_ypoc",
         "px_vs_dpoc", "pshape", "tail_up", "tail_dn", "px_in_range", "rng_rel", "ypoc_dist"]
ZONE = ["zone_px", "zone_open", "zone_up", "zone_dn"]
TARGETS = {  # цель → (название, признак-расстояние до цели, колонка цены уровня)
    "y_poc": ("POC вчера", None, None), "y_vah": ("VAH вчера", None, None), "y_val": ("VAL вчера", None, None),
    "y_high": ("High вчера", None, None), "y_low": ("Low вчера", None, None),
    "npoc_up": ("nPOC сверху", "npoc_up_dist", "npoc_up_px"), "npoc_dn": ("nPOC снизу", "npoc_dn_dist", "npoc_dn_px"),
    "blue_up": ("синяя сверху", "blue_up_dist", "blue_up_px"), "blue_dn": ("синяя снизу", "blue_dn_dist", "blue_dn_px"),
    "ext_up": ("новый High дня", None, None), "ext_dn": ("новый Low дня", None, None),
}
N_PLAC = 3
_G = {}


def _init(*a):
    _G.update(zip(("days", "by_day", "snaps", "npocs", "tick", "shapes"), a))


def _one(i):
    day = _G["days"].loc[i]
    g, z = _G["by_day"][day.date], _G["snaps"][day.date]
    args = (_G["npocs"].get(day.date, np.array([])), _G["tick"], day.rng_avg20, day.ib_avg20, _G["shapes"][day.date])
    real = panel_day(day, g, z, *args)
    rng = np.random.default_rng(int(day.date.strftime("%Y%m%d")))
    plac = []
    for run in range(N_PLAC):
        sh = np.round(rng.uniform(1.5, 6.0) * rng.choice([-1, 1]) * 4) / 4
        zz = z.copy()
        zz["lo"] += sh
        zz["hi"] += sh
        for r in panel_day(day, g, zz, *args):
            plac.append({"date": r["date"], "k": r["k"], "run": run, **{c: r[c] for c in
                         ZONE + ["zu_dist", "zd_dist", "close_in_open_zone"] if c in r}})
    return real, plac


def build(cfg, tick):
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    _, profiles = daily_profiles(bars, cfg, tick)
    typed = classify_days(days[["date", "n_periods", "short_day", "open", "high", "low", "close", "volume", "ib_high",
                                "ib_low", "poc", "vah", "val", "total_tpo", "single_prints", "tail_up_ticks",
                                "tail_down_ticks", "poor_high", "poor_low"]].copy(), profiles, cfg, tick)
    days["y_pshape"] = typed.profile_shape.shift(1).to_numpy()
    days["rng_avg20"] = (days.high - days.low).where(~days.short_day).rolling(20, min_periods=10).mean().shift(1)
    snaps = load_snaps(OUT)
    snap_by = {d: g.reset_index(drop=True) for d, g in snaps.groupby("day")}
    by_day = {d: g.sort_values("period").reset_index(drop=True) for d, g in bars.groupby("date")}
    npocs = naked_pocs(days)
    ok = days[(~days.short_day) & (days.y_short_day == False) & (~days.roll_day) & days.ib_avg20.notna()
              & days.rng_avg20.notna() & days.date.isin(snap_by.keys())]
    ok = ok[[len(by_day[d]) == 13 for d in ok.date]]
    shapes = dict(zip(days.date, days.y_pshape))
    with Pool(4, initializer=_init, initargs=(days, by_day, snap_by, npocs, tick, shapes)) as pool:
        res = pool.map(_one, list(ok.index), chunksize=8)
    real = pd.DataFrame([r for a, _ in res for r in a])
    plac = pd.DataFrame([r for _, b in res for r in b])
    real.to_csv(OUT / "day_status.csv", index=False)
    plac.to_csv(OUT / "day_status_placebo.csv", index=False)
    return real, plac


def X_of(df, cols):
    num = [c for c in cols if c in ("px_in_range", "rng_rel", "ypoc_dist", "zu_dist", "zd_dist", "npoc_up_dist", "npoc_dn_dist",
                                 "blue_up_dist", "blue_dn_dist")]
    cat = [c for c in cols if c not in num]
    X = pd.get_dummies(df[cat].astype(str), dtype=float)
    for c in num:
        X[c] = df[c].astype(float).fillna(9.0)
    return X


class Blend:
    """Среднее бустинга и логистической регрессии: на 2025+ лучше или наравне с каждой по AUC и Brier по всем целям
    (логрегрессия калибрована лучше, бустинг ловит нелинейность — POC вчера AUC 0.82 против 0.75)."""

    def fit(self, X, y):
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.linear_model import LogisticRegression
        self.m = [HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=60,
                                                 l2_regularization=1.0, random_state=0).fit(X, y),
                  LogisticRegression(max_iter=4000, C=0.3).fit(X, y)]
        return self

    def predict_proba(self, X):
        return sum(m.predict_proba(X) for m in self.m) / len(self.m)


def fit(df, tg, cols, split):
    m = df[tg].notna()
    d = df[m]
    X = X_of(d, cols)
    tr = (d.date < split).to_numpy()
    clf = Blend().fit(X[tr], d[tg][tr])
    return clf, X.columns, d, X, tr


def evaluate(real, plac, split):
    from sklearn.metrics import brier_score_loss, roc_auc_score
    print("\nAUC на 2025+ (обучение до 2025): локально | + зоны 1.8 | + зоны сдвинутой карты (3 прогона)   · база цели на 2025+")
    calib = []
    for tg, (name, dist, _) in TARGETS.items():
        loc = LOCAL + ([dist] if dist else [])
        zon = loc + ZONE + [c for c in ("zu_dist", "zd_dist") if c != dist]
        out = []
        for cols, frame in ((loc, real), (zon, real)):
            clf, cols_, d, X, tr = fit(frame, tg, cols, split)
            p = clf.predict_proba(X[~tr])[:, 1]
            out.append(roc_auc_score(d[tg][~tr], p))
            if cols is loc:
                calib.append((name, d[tg][~tr].to_numpy(), p))
        pl = []
        for run in range(N_PLAC):
            f = real.copy()
            pr = plac[plac.run == run].set_index(["date", "k"])
            key = pd.MultiIndex.from_frame(f[["date", "k"]])
            for c in ZONE + ["zu_dist", "zd_dist"]:
                f[c] = pr[c].reindex(key).to_numpy()
            clf, cols_, d, X, tr = fit(f, tg, zon, split)
            pl.append(roc_auc_score(d[tg][~tr], clf.predict_proba(X[~tr])[:, 1]))
        base = real[real.date >= split][tg].mean()
        print(f"  {name:18s} {out[0]:.3f} | {out[1]:.3f} | {min(pl):.3f}–{max(pl):.3f}   · {base:5.1%}  (n={real[tg].notna().sum()})")
    print("\nКалибровка «локально» на 2025+: обещанная вероятность → сбылось (n)")
    for name, y, p in calib:
        bins = pd.cut(p, [0, .1, .25, .4, .6, .75, .9, 1.0])
        t = pd.DataFrame({"y": y, "b": bins}).groupby("b", observed=True).y.agg(["mean", "size"])
        print(f"  {name:18s} " + "  ".join(f"{iv.left:.0%}–{iv.right:.0%}: {r['mean']:.0%} ({int(r['size'])})"
                                           for iv, r in t.iterrows() if r["size"] >= 30))


def open_zone(real, plac, split):
    print("\nОткрылись внутри зоны 1.8 → закрытие дня в этой зоне (до 2025 / 2025+) против сдвинутой карты")
    r = real[real.k == 0]
    p = plac[plac.k == 0]
    for c in ("синяя", "зелёная", "красная", "серая"):
        a, b = r[r.zone_open == c], p[p.zone_open == c]
        cells = []
        for m_a, m_b in ((a.date < split, b.date < split), (a.date >= split, b.date >= split)):
            cells.append(f"{a[m_a].close_in_open_zone.mean():5.1%} (n={m_a.sum():3d}) vs {b[m_b].close_in_open_zone.mean():5.1%}")
        print(f"  {c:8s} " + " | ".join(cells))


def card(real, date, split):
    date = pd.Timestamp(date)
    d = real[real.date == date].sort_values("k")
    if d.empty:
        print("нет дня", date)
        return
    probs = {}
    for tg, (name, dist, pxc) in TARGETS.items():
        cols = LOCAL + ([dist] if dist else [])
        clf, cols_, dd, X, tr = fit(real, tg, cols, split)
        x = X_of(d, cols).reindex(columns=cols_, fill_value=0.0)
        probs[tg] = clf.predict_proba(x)[:, 1]
    print(f"\nСтатус {date:%d.%m.%Y} (модель учится на днях до {split:%Y-%m-%d}); [✓] — цель потом взята, [ ] — нет, · — уже взята")
    for i, (_, r) in enumerate(d.iterrows()):
        head = (f"\nпосле {r.moment}: цена {'в VA' if r.px_vs_yva == 'в VA' else r.px_vs_yva} вчера, "
                f"{'выше' if r.px_vs_dpoc == 'выше' else 'ниже'} POC дня, форма {r.pshape}, кто обновляет: {r.control:+d}, "
                f"последний период — {r.last}; зона под ценой: {r.zone_px}")
        print(head)
        parts = []
        for tg, (name, dist, pxc) in TARGETS.items():
            v = r[tg]
            if pd.isna(v):
                parts.append(f"{name} ·")
                continue
            lvl = f" {r[pxc]:.2f}" if pxc and not pd.isna(r[pxc]) else ""
            parts.append(f"{name}{lvl} {probs[tg][i]:.0%} [{'✓' if v == 1 else ' '}]")
        print("   " + " | ".join(parts))


def main(*args):
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    if (OUT / "day_status.csv").exists() and "build" not in args:
        real = pd.read_csv(OUT / "day_status.csv", parse_dates=["date"])
        plac = pd.read_csv(OUT / "day_status_placebo.csv", parse_dates=["date"])
    else:
        real, plac = build(cfg, tick)
    if args and args[0] == "card":
        card(real, args[1], split)
        return
    print(f"дней {real.date.nunique()}, строк (день × момент) {len(real)}")
    evaluate(real, plac, split)
    open_zone(real, plac, split)


if __name__ == "__main__":
    main(*sys.argv[1:])
