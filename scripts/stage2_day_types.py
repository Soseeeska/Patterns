"""Этап 2: типы дня по Далтону + форма профиля + миграция value.

Запуск:  python -m scripts.stage2_day_types [ES] [N_SAMPLE] [SEED]
Результат: output/<INSTR>_day_types.csv,
           output/<INSTR>_day_types_review.csv  (дни на проверку, колонка your_label пустая)
           output/<INSTR>_day_types_review_<k>.png
"""
from __future__ import annotations

import sys

import pandas as pd

from scripts.stage1_profiles import load_adjusted
from tpo.data import ROOT, load_config
from tpo.day_type import classify_days
from tpo.plot import draw_days


def main(instrument: str = "ES", n_sample: str = "30", seed: str = "7") -> None:
    cfg = load_config(instrument=instrument)
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    bars, daily, profiles, _, tick = load_adjusted(instrument, cfg)
    d = classify_days(daily, profiles, cfg, tick)
    d.to_csv(out / f"{instrument}_day_types.csv", index=False, float_format="%.3f")

    full = d[d.day_type != "Short"]
    print(f"{instrument}: {len(full)} полных дней\n")
    for col in ["day_type", "profile_shape", "value_migration"]:
        vc = full[col].value_counts()
        print(pd.DataFrame({"дней": vc, "%": (100 * vc / vc.sum()).round(1)}).to_string(), "\n")

    # Выборка на проверку: случайные дни, но не меньше 2 каждого типа (если есть).
    n = int(n_sample)
    parts = [g.sample(min(2, len(g)), random_state=int(seed)) for _, g in full.groupby("day_type")]
    picked = pd.concat(parts)
    rest = full.drop(picked.index).sample(max(n - len(picked), 0), random_state=int(seed))
    review = pd.concat([picked, rest]).sort_values("date").head(n)

    cols = ["date", "day_type", "profile_shape", "re_direction", "range_vs_ib", "range_vs_avg",
            "close_position", "dd_gap_ticks", "value_migration"]
    r = review[cols].copy()
    r["your_label"] = ""
    r.to_csv(out / f"{instrument}_day_types_review.csv", index=False, float_format="%.2f")

    dates = review["date"].tolist()
    titles = [f"{x.date:%Y-%m-%d}\n{x.day_type} | {x.profile_shape} | RE {x.re_direction}\n"
              f"range/IB {x.range_vs_ib:.2f}, close {x.close_position:.2f}"
              for x in review.itertuples()]
    for k in range(0, len(dates), 10):
        draw_days(bars, d, dates[k:k + 10], tick,
                  out / f"{instrument}_day_types_review_{k // 10 + 1}.png",
                  titles=titles[k:k + 10], ncols=5,
                  header=f"{instrument}: проверка типов дня, часть {k // 10 + 1}")
    print(r.to_string(index=False, float_format=lambda x: f"{x:.2f}",
                      formatters={"date": lambda x: f"{x:%Y-%m-%d}"}))


if __name__ == "__main__":
    main(*sys.argv[1:])
