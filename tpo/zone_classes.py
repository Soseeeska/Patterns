"""Класс зоны по 30m-взаимодействиям.

Бар взаимодействует с зоной [lo, hi], если его диапазон её задевает. Типы:
  long  — лонг-отторжение: нижний хвост пересёк границу зоны, close выше зоны;
  short — шорт-отторжение: верхний хвост пересёк границу, close ниже зоны;
  range — проторговка: close внутри зоны (в т.ч. хвост наружу и возврат
          внутрь — отторжение изнутри) или пробитие: open и close по разные
          стороны зоны.
Остальные касания не считаются. Класс зоны — тип, которого больше
(поддержка / сопротивление / проторговка); при равенстве — проторговка.
Вариант «по отторжениям»: решает доля лонг- или шорт-отторжений среди всех
отторжений (rejection_share), close внутри не учитываются.
"""
from __future__ import annotations

import numpy as np

from tpo.wicks import BOUNDARY, wick_positions

SUPPORT, RESISTANCE, RANGE = "поддержка", "сопротивление", "проторговка"


def interaction_types(o, h, l, c, lo: float, hi: float, min_wick: float) -> np.ndarray:
    """Массив типов по барам: 'long' / 'short' / 'range' / '' (нет взаимодействия)."""
    lw, uw = wick_positions(o, h, l, c, lo, hi, min_wick)
    touch = (l <= hi) & (h >= lo)
    inside = (c >= lo) & (c <= hi)
    through = ((o > hi) & (c < lo)) | ((o < lo) & (c > hi))
    out = np.full(len(o), "", dtype=object)
    out[touch & (inside | through)] = "range"
    out[touch & (lw == BOUNDARY) & (c > hi)] = "long"
    out[touch & (uw == BOUNDARY) & (c < lo)] = "short"
    return out


def classify(types, rejection_share: float | None = None) -> str:
    """rejection_share=None — большинство из трёх типов. Иначе считаются только
    отторжения: лонг ≥ rejection_share от (лонг + шорт) — поддержка, шорт ≥
    rejection_share — сопротивление, иначе проторговка."""
    n_l = int(np.sum(types == "long"))
    n_s = int(np.sum(types == "short"))
    n_r = int(np.sum(types == "range"))
    if rejection_share is not None:
        tot = n_l + n_s
        if tot and n_l / tot >= rejection_share:
            return SUPPORT
        if tot and n_s / tot >= rejection_share:
            return RESISTANCE
        return RANGE
    if n_l > max(n_s, n_r):
        return SUPPORT
    if n_s > max(n_l, n_r):
        return RESISTANCE
    return RANGE
