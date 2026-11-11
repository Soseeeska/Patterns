"""Этап 7: статистика по торговым заметкам (открытие, A, IB, переоценка, hidden levels).

Запуск:  python -m scripts.stage7_patterns [ES]
Результат: output/<INSTR>_patterns_days.csv, output/<INSTR>_patterns_report.md
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.day_type import classify_days
from tpo.patterns import day_features, hidden_levels, rotation_breaks

LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def table(df: pd.DataFrame, title: str):
    say(f"\n**{title}**\n")
    say(df.to_markdown(floatfmt=".1f"))


def pct(s):
    return 100 * s.mean()


def outcome_table(f: pd.DataFrame, by: str) -> pd.DataFrame:
    base = f.assign(**{by: "ВСЕ ДНИ (база)"})
    x = pd.concat([f, base])
    g = x.groupby(by, observed=True)
    return pd.DataFrame({
        "дней": g.size(),
        "range/средний, мед.": g.range_vs_avg.median().map(lambda v: f"{v:.2f}×"),
        "выход за IB в одну сторону %": g.re_direction.apply(lambda s: pct(s.isin(["up", "down"]))),
        "выход в обе %": g.re_direction.apply(lambda s: pct(s == "both")),
        "без выхода %": g.re_direction.apply(lambda s: pct(s == "none")),
        "Trend+DD %": g.day_type.apply(lambda s: pct(s.isin(["Trend", "Double Distribution"]))),
        "Normal+Non-trend %": g.day_type.apply(lambda s: pct(s.isin(["Normal", "Non-trend"]))),
        "Neutral %": g.day_type.apply(lambda s: pct(s.str.startswith("Neutral"))),
    })


def main(instrument: str = "ES") -> None:
    cfg = load_config()
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    days = classify_days(daily, profiles, cfg, tick)
    f = day_features(bars, days, cfg, tick)
    f = f[f.range_vs_avg.notna()]
    f.to_csv(ROOT / "output" / f"{instrument}_patterns_days.csv", index=False)
    say(f"# Статистика по заметкам — {instrument}, RTH 30m\n")
    say(f"Дней: {len(f)} ({f.date.min():%Y-%m-%d} – {f.date.max():%Y-%m-%d}). "
        "«База» — те же показатели по всем дням.")

    say("\n## 1. Expansion potential по месту открытия")
    table(outcome_table(f, "open_loc"), "Открытие относительно прошлого дня")

    say("\n## 2. Открылись в балансе → POC прошлого дня")
    b = f[f.open_loc.str.startswith("в балансе")]
    g = b.groupby("a_accept")
    t2 = pd.DataFrame({"дней": g.size(), "дошли до POC прошлого дня %": g.poc_touched.apply(pct),
                       "из них уже в A %": g.apply(lambda s: pct(s.poc_period[s.poc_touched] == 0),
                                                   include_groups=False),
                       "до конца IB (A–B) %": g.apply(lambda s: pct(s.poc_period.between(0, 1)),
                                                      include_groups=False)})
    t2.loc["все открытия в балансе"] = [len(b), pct(b.poc_touched), pct(b.poc_period[b.poc_touched] == 0),
                                        pct(b.poc_period.between(0, 1))]
    o = f[~f.open_loc.str.startswith("в балансе")]
    t2.loc["для сравнения: открытия вне VA"] = [len(o), pct(o.poc_touched), pct(o.poc_period[o.poc_touched] == 0),
                                                pct(o.poc_period.between(0, 1))]
    table(t2, "Касание POC прошлого дня")
    f["dist_bucket"] = pd.cut(f.open_poc_dist, [0, 0.1, 0.2, 0.35, 0.6, np.inf],
                              labels=["<0.1", "0.1–0.2", "0.2–0.35", "0.35–0.6", ">0.6"])
    f["where"] = np.where(f.open_loc.str.startswith("в балансе"), "open внутри VA", "open вне VA")
    t2b = (f.groupby(["dist_bucket", "where"], observed=True).poc_touched
           .agg(["size", lambda s: pct(s)]).unstack())
    t2b.columns = [f"{a} · {b}" for a, b in t2b.columns]
    t2b = t2b.rename(columns=lambda c: c.replace("size", "дней").replace("<lambda_0>", "до POC %"))
    table(t2b, "Контроль: то же при одинаковом расстоянии open → POC прошлого дня (в средних дневных диапазонах)")

    say("\n## 3. Принятие / отторжение в периоде A")
    f["open_x_a"] = f.open_loc.str.split(" ").str[0] + " · " + f.a_accept.str.split(" ").str[1]
    table(outcome_table(f, "open_x_a"), "Место открытия × период A (принят = close A внутри VA прошлого дня)")

    say("\n## 4. Ширина IB (к среднему IB за 20 дней)")
    table(outcome_table(f, "ib_bucket"), "Ширина IB")

    say("\n## 5. Неудавшийся выход за IB")
    fl = f[f.ib_fail != ""]
    g = fl.groupby(["ib_fail", "ib_bucket"], observed=True)
    t5 = pd.DataFrame({"дней": g.size(), "дошли до противоположного края IB %": g.ib_fail_reach_opp.apply(pct)})
    t5.loc[("все", ""), :] = [len(fl), pct(fl.ib_fail_reach_opp)]
    table(t5, f"После выхода за IB на ≥{cfg['patterns']['ib_fail_min_ext_ticks']} тиков период закрылся обратно "
              "внутри IB")
    ext_days = f[f.re_direction.isin(["up", "down", "both"])]
    say(f"\nДля сравнения: дней с любым выходом за IB {len(ext_days)}, из них с выходом в обе стороны "
        f"{pct(ext_days.re_direction == 'both'):.1f}%.")

    say("\n## 6. Последний период (M) прошлого дня и гэп")
    gp = f[f.gap_kind != "без гэпа"]
    g = gp.groupby(["gap_kind", "m_with_gap"])
    t6 = pd.DataFrame({"дней": g.size(), "гэп закрыт (дошли до close прошлого дня) %": g.gap_filled.apply(pct),
                       "день в сторону гэпа %": g.apply(lambda s: pct(s.day_dir == s.gap_dir),
                                                        include_groups=False)})
    t6.index = [f"{k} · M {'по ходу гэпа' if w else 'против гэпа'}" for k, w in t6.index]
    table(t6, f"Гэп ≥{cfg['patterns']['gap_min_ticks']} тиков от close прошлого дня; M — направление последнего "
              "периода прошлого дня")

    say("\n## 10. Смена ротации: период закрылся за диапазоном всех прошлых периодов дня")
    r = rotation_breaks(bars, days, cfg, tick)
    r = r[~r.last_period]
    r["время"] = pd.cut(r.period, [0, 5, 9, 13], labels=["C–F", "G–J", "K–M"], right=False)
    g = r.groupby(["время", "side"], observed=True)
    t10 = pd.DataFrame({"событий": g.size(), "следующий период подтвердил %": g.next_confirms.apply(pct),
                        "день закрылся за уровнем %": g.day_close_beyond.apply(pct),
                        "вернулись в середину диапазона %": g.back_to_mid.apply(pct)})
    table(t10, "Первый такой период в каждую сторону за день (не раньше периода C)")

    say("\n## 11. Миграция value")
    f["m1"] = f.prev_migration.replace({"overlap_higher": "higher*", "overlap_lower": "lower*"})
    f["vm"] = f.value_migration.replace({"overlap_higher": "higher*", "overlap_lower": "lower*"})
    t11 = pd.crosstab(f.m1, f.vm, normalize="index") * 100
    t11["дней"] = f.groupby("m1").size()
    base = f.vm.value_counts(normalize=True) * 100
    t11.loc["ВСЕ ДНИ (база)"] = list(base.reindex(t11.columns[:-1]).fillna(0)) + [len(f)]
    table(t11, "Строка — миграция вчера, столбец — сегодня, % (higher* / lower* — с перекрытием)")

    say("\n## 7. Hidden levels (первый тест, правило этапа 6)")
    h = hidden_levels(bars, days, profiles, cfg, tick)
    h = h[~h.breakout_day] if "breakout_day" in h else h
    g = h.groupby("kind")
    t7 = pd.DataFrame({"тестов": g.size(), "удержание %": g.outcome.apply(lambda s: pct(s == "hold")),
                       "отход ≥16 тиков %": g.reaction_ticks.apply(lambda s: pct(s >= 16)),
                       "отход, мед. тиков": g.reaction_ticks.median(),
                       "прокол, мед. тиков": g.adverse_ticks.median()})
    table(t7, "VAH/VAL/POC прошлых дней (до 20 дней назад), прошитые single print, против проторгованных "
              "уровней и чистых single prints")

    out = ROOT / "output" / f"{instrument}_patterns_report.md"
    out.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\n{out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
