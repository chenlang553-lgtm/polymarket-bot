from collections import deque
from math import log, sqrt


class RollingState(object):
    def __init__(self, strategy_config):
        self.strategy_config = strategy_config
        self.open_price = None
        self.last_price = None
        self.ewma_var = 0.0
        self.returns_1s = deque(maxlen=120)
        self.x_history = deque(maxlen=360)

    def update_price(self, price):
        if price <= 0:
            raise ValueError("price must be positive")
        if self.open_price is None:
            self.open_price = price
        if self.last_price is not None:
            ret = log(price / self.last_price)
            self.returns_1s.append(ret)
            lam = self.strategy_config.sigma_slow_lambda
            self.ewma_var = lam * self.ewma_var + (1.0 - lam) * (ret * ret)
        self.last_price = price
        x_t = log(price / self.open_price)
        self.x_history.append(x_t)
        return x_t

    def _window_std(self, count):
        if len(self.returns_1s) < 2:
            return 1e-6
        values = list(self.returns_1s)[-count:]
        if len(values) < 2:
            return 1e-6
        mean = sum(values) / len(values)
        var = sum((item - mean) ** 2 for item in values) / max(1, len(values) - 1)
        return max(1e-6, sqrt(var))

    def sigma_10(self):
        return self._window_std(10)

    def sigma_30(self):
        return self._window_std(30)

    def sigma_slow(self):
        return max(1e-6, sqrt(max(self.ewma_var, 1e-12)))

    def momentum(self, seconds):
        if len(self.x_history) <= seconds:
            return 0.0
        current = self.x_history[-1]
        previous = self.x_history[-seconds - 1]
        return (current - previous) / float(seconds)

    def latest_x(self):
        return self.x_history[-1] if self.x_history else 0.0

    def max_recent_abs_return(self, count):
        if not self.returns_1s:
            return 0.0
        return max(abs(item) for item in list(self.returns_1s)[-count:])
