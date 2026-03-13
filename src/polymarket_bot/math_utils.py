from __future__ import annotations

from math import erf, sqrt


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def logit(probability: float) -> float:
    probability = clamp(probability, 1e-6, 1.0 - 1e-6)
    return log(probability / (1.0 - probability))


def logistic(value: float) -> float:
    if value >= 0:
        exp_neg = exp(-value)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = exp(value)
    return exp_pos / (1.0 + exp_pos)


from math import exp, log  # noqa: E402
