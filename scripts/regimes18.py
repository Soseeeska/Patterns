"""Режимы относительно балансов 1.8 (глобальный контекст) × локальный контекст Далтона × цвета зон.

Вход — снимки ZoneSnap в output/z18 (snap_*.csv, comps_*.csv, rolls.csv) и события scripts.zones18_mp (events.csv).
Режим дня — tpo/regimes.py (к открытию RTH, без заглядывания вперёд). Поиск — до split_date, проверка — после.

  A. исходы дня по режимам против всех дней;
  B. режим × место открытия → вчерашний POC и день по направлению;
  C. добавляет ли режим прогноз сверх локального контекста (логистическая регрессия, обучение до 2025, AUC на 2025+);
  D. цвета зон внутри режима: удержание первого теста по цвету × роли, по направлению режима и против;
  E. P / b вчера + открытие за VA + тест «пуза» + отторжение → день по направлению смещения value.

Запуск:  python -m scripts.regimes18 [chart]   (chart — балансы в ценах графика, как в MotiveWave, без сдвига на роллы)
Результат: output/z18/regimes.csv / regimes_chart.csv (день × режим × исходы), отчёт в stdout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.zones18_mp import OUT, enrich
from tpo.data import load_bars, load_config
from tpo.day_type import classify_days
from tpo.profile import daily_profiles
from tpo.regimes import REGIMES, adjusted_closes, day_outcomes, load_comps, regime_of_day, roll_gaps
from tpo.zones18 import day_table, open_loc


def build(cfg, tick, chart=False):
    bars = load_bars("ES", cfg)
    days = day_table(bars, cfg, tick)
    _, profiles = daily_profiles(bars, cfg, tick)
    typed = classify_days(days[["date", "n_periods", "short_day", "open", "high", "low", "close", "volume", "ib_high",
                                "ib_low", "poc", "vah", "val", "total_tpo", "single_prints", "tail_up_ticks",
                                "tail_down_ticks", "poor_high", "poor_low"]].copy(), profiles, cfg, tick)
    days["pshape"] = typed.profile_shape.to_numpy()
    days["day_type"] = typed.day_type.to_numpy()
    days["y_shape"] = days.pshape.shift(1)
    days["y_day_type"] = days.day_type.shift(1)
    days["rng_avg20"] = (days.high - days.low).where(~days.short_day).rolling(20, min_periods=10).mean().shift(1)
    comps = load_comps(OUT, chart)
    rolls = roll_gaps(OUT / "rolls.csv", cfg["data"]["timezone"])
    if chart:
        rolls = rolls.iloc[0:0]
    by_day = {d: g.sort_values("period").reset_index(drop=True) for d, g in bars.groupby("date")}
    comp_by = {d: g for d, g in comps.groupby("day")}
    ok = days[(~days.short_day) & (days.y_short_day == False) & (~days.roll_day) & days.ib_avg20.notna()
              & days.date.isin(comp_by.keys())]
    ok = ok[[len(by_day[d]) == 13 for d in ok.date]]
    rows = []
    for _, day in ok.iterrows():
        D = day.date
        hist = adjusted_closes(days[~days.short_day], rolls, D)
        r = regime_of_day(D, hist, comp_by[D])
        r.update(day_outcomes(r, day, by_day[D]))
        g = by_day[D]
        r.update({"open_loc": open_loc(day.open, day), "y_shape": day.y_shape, "y_day_type": day.y_day_type,
                  "gap": "вверх" if day.open > day.y_close else "вниз",
                  "open_vs_home": ("—" if np.isnan(r["home_core_lo"]) else "над ядром" if day.open > r["home_core_hi"]
                                   else "под ядром" if day.open < r["home_core_lo"] else "в ядре"),
                  "dist_core": (np.nan if np.isnan(r["home_core_lo"]) else
                                (day.open - np.clip(day.open, r["home_core_lo"], r["home_core_hi"])) / day.rng_avg20),
                  "y_vah": day.y_vah, "y_val": day.y_val, "y_high_px": day.y_high, "y_low_px": day.y_low,
                  "open": day.open, "close": day.close, "ib_lo": g.low[:2].min(), "ib_hi": g.high[:2].max(),
                  "b_close": g.close[1], "a_close": g.close[0], "high": g.high.max(), "low": g.low.min()})
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / ("regimes_chart.csv" if chart else "regimes.csv"), index=False)
    return df


def pct(x):
    return "   —  " if pd.isna(x) else f"{x:6.1%}"


def rates(df, by, cols, split, min_n=25, base=None):
    print(f"  {'':40s} " + " ".join(f"{c:>21s}" for c in cols))
    for key, g in df.groupby(by, sort=False):
        cells = []
        n0 = n1 = 0
        for c in cols:
            a, b = g[g.date < split][c].dropna(), g[g.date >= split][c].dropna()
            n0, n1 = max(n0, len(a)), max(n1, len(b))
            cells.append(f"{pct(a.mean() if len(a) >= min_n else np.nan)} / {pct(b.mean() if len(b) >= min_n else np.nan)}  ")
        name = key if isinstance(key, str) else " · ".join(map(str, key))
        print(f"  {name:40s} " + " ".join(f"{x:>21s}" for x in cells) + f"  n={n0}/{n1}")
    if base is not None:
        cells = [f"{pct(base[base.date < split][c].mean())} / {pct(base[base.date >= split][c].mean())}  " for c in cols]
        print(f"  {'все дни':40s} " + " ".join(f"{x:>21s}" for x in cells))


def model_check(df, split):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    local = ["open_loc", "y_shape", "y_day_type", "gap"]
    glob = ["regime", "dir", "open_vs_home"]
    rng = np.random.default_rng(1)
    print("\nC. AUC на 2025+ (обучение до 2025): локально | локально + режим | + перемешанный режим (5 раз)")
    for tg, name in (("close_up", "день вверх (close > open)"), ("y_poc", "POC вчера"), ("y_high", "снять High вчера"),
                     ("y_low", "снять Low вчера"), ("home_core", "ядро своего баланса")):
        m = df[tg].notna()
        d = df[m]
        tr, te = d.date < split, d.date >= split

        def auc(cols, frame):
            X = pd.get_dummies(frame[cols].astype(str), dtype=float)
            clf = LogisticRegression(max_iter=3000, C=0.5).fit(X[tr], d.loc[tr, tg])
            return roc_auc_score(d.loc[te, tg], clf.predict_proba(X[te])[:, 1])
        a0, a1 = auc(local, d), auc(local + glob, d)
        sh = []
        for _ in range(5):
            f = d.copy()
            perm = rng.permutation(len(f))
            for c in glob:
                f[c] = f[c].to_numpy()[perm]
            sh.append(auc(local + glob, f))
        print(f"  {name:30s} {a0:.3f} | {a1:.3f} | {min(sh):.3f}–{max(sh):.3f}   (n {int(tr.sum())}/{int(te.sum())})")


def colors_in_regime(df, split):
    ev = pd.read_csv(OUT / "events.csv", parse_dates=["date"])
    ev = enrich(ev, split).merge(df[["date", "regime", "dir"]], on="date")
    # по направлению режима: поддержка при движении вверх — «по направлению»
    sgn = np.where(ev.dir == "вверх", 1, np.where(ev.dir == "вниз", -1, 0))
    rs = np.where(ev.role == "поддержка", 1, -1)
    ev["vs_dir"] = np.select([sgn == 0, sgn * rs > 0], ["—", "по направлению"], "против направления")
    ev["cr"] = ev.role_color + " как " + ev.role
    print("\nD. Удержание первого теста зоны: цвет × роль внутри режима (до 2025 / 2025+)")
    for grp, regs in (("в балансе (формирующийся или старый)", ["формирующийся баланс", "внутри старого баланса"]),
                      ("отвергли выход / возврат", ["отвергли выход", "возврат в старый баланс"]),
                      ("снаружи (выход, принят, discovery)", ["выход, не принят", "приняты снаружи → к балансу", "price discovery"])):
        e = ev[ev.regime.isin(regs)]
        print(f"\n  — {grp}: тестов {len(e)}")
        rates(e, "cr", ["hold"], split, min_n=40)
        if grp != "в балансе (формирующийся или старый)":
            print("    по направлению режима:")
            rates(e, ["vs_dir", "role_color"], ["hold"], split, min_n=40)
    return ev


def p_shape(df, split):
    print("\nE. P / b вчера, открытие за VA вчера в сторону смещения, тест VA вчера в IB, отторжение (B закрылся снаружи VA)")
    d = df.copy()
    up = d.open > d.y_vah
    dn = d.open < d.y_val
    test_up = up & (d.ib_lo <= d.y_vah)
    test_dn = dn & (d.ib_hi >= d.y_val)
    rej_up = test_up & (d.b_close > d.y_vah)
    rej_dn = test_dn & (d.b_close < d.y_val)
    d["y_hi_taken"] = (d.high > d.y_high_px).astype(float)
    d["y_lo_taken"] = (d.low < d.y_low_px).astype(float)
    d["c_above_yh"] = (d.close > d.y_high_px).astype(float)
    d["c_below_yl"] = (d.close < d.y_low_px).astype(float)
    d["up_day"] = (d.close > d.open).astype(float)
    d["dn_day"] = (d.close < d.open).astype(float)
    rows = []
    for name, m, shp in (("открытие выше VA, тест, отторжение", rej_up, "P"),
                         ("открытие выше VA, тест, приняли (B в VA)", test_up & ~rej_up, "P"),
                         ("открытие выше VA, без теста VA в IB", up & ~test_up, "P")):
        for s in ("P", "D", "b", "thin", "B"):
            g = d[m & (d.y_shape == s)]
            rows.append((name, f"вчера {s}", g))
    print(f"  {'':52s} {'High вчера снят':>17s} {'close > High вч.':>17s} {'день вверх':>17s}   n")
    for name, sh, g in rows:
        if len(g) < 15:
            continue
        a, b = g[g.date < split], g[g.date >= split]
        f = lambda c: f"{pct(a[c].mean() if len(a) >= 10 else np.nan)}/{pct(b[c].mean() if len(b) >= 10 else np.nan)}"
        print(f"  {name + ' · ' + sh:52s} {f('y_hi_taken'):>17s} {f('c_above_yh'):>17s} {f('up_day'):>17s}   {len(a)}/{len(b)}")
    rows = []
    for name, m in (("открытие ниже VA, тест, отторжение", rej_dn), ("открытие ниже VA, тест, приняли (B в VA)", test_dn & ~rej_dn),
                    ("открытие ниже VA, без теста VA в IB", dn & ~test_dn)):
        for s in ("b", "D", "P", "thin", "B"):
            rows.append((name, f"вчера {s}", d[m & (d.y_shape == s)]))
    print(f"\n  {'':52s} {'Low вчера снят':>17s} {'close < Low вч.':>17s} {'день вниз':>17s}   n")
    for name, sh, g in rows:
        if len(g) < 15:
            continue
        a, b = g[g.date < split], g[g.date >= split]
        f = lambda c: f"{pct(a[c].mean() if len(a) >= 10 else np.nan)}/{pct(b[c].mean() if len(b) >= 10 else np.nan)}"
        print(f"  {name + ' · ' + sh:52s} {f('y_lo_taken'):>17s} {f('c_below_yl'):>17s} {f('dn_day'):>17s}   {len(a)}/{len(b)}")
    # P-отторжение × режим
    d["p_rej"] = np.select([rej_up & (d.y_shape == "P"), rej_dn & (d.y_shape == "b")], ["P↑ отторжение", "b↓ отторжение"], "")
    s = d[d.p_rej != ""]
    print("\n  P↑ / b↓ с отторжением по режиму (день по направлению смещения):")
    s["with"] = np.where(s.p_rej.str.startswith("P"), s.up_day, s.dn_day)
    rates(s, ["p_rej", "regime"], ["with"], split, min_n=8)


def main(*args):
    chart = "chart" in args
    cfg = load_config(instrument="ES")
    tick = cfg["data"]["instruments"]["ES"]["tick_size"]
    split = pd.Timestamp(cfg["day_context"]["split_date"])
    df = build(cfg, tick, chart)
    print("цены:", "графика (без сдвига на роллы)" if chart else "контракта дня (старые балансы сдвинуты на роллы)")
    df["regime"] = pd.Categorical(df.regime, REGIMES, ordered=True)
    df = df.sort_values(["regime", "date"])
    df["regime"] = df.regime.astype(str)
    print(f"дней {len(df)} ({df.date.min():%Y-%m-%d} … {df.date.max():%Y-%m-%d}); ячейка: до 2025 / 2025+")
    print("\nA. Режим к открытию → исходы дня")
    print("  режимы:", df.groupby("regime", sort=False).size().to_dict())
    rates(df, "regime", ["y_poc", "home_core", "close_up"], pd.Timestamp(split), base=df)
    print("\n  направленные режимы: dir_close — день по направлению режима (выход вверх → вверх; отвергли / возврат сверху → вниз)")
    rates(df[df.dir != "—"], ["regime", "dir"], ["dir_close", "back_in", "ext", "far_va", "tgt_edge"], split)
    print("\nB. Режим × место открытия → POC вчера / ядро своего баланса")
    rates(df, ["regime", "open_loc"], ["y_poc", "home_core"], split, min_n=20)
    model_check(df, split)
    colors_in_regime(df, split)
    p_shape(df, split)


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:])
