"""A small Prometheus-compatible metrics registry (no external dependency).

State is per process. With several replicas Prometheus scrapes each pod separately and
sums them, which is the normal model for counters and histograms.
"""

import threading
from collections import defaultdict

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0)
_Labels = tuple[tuple[str, str], ...]


def _labels(**labels: str) -> _Labels:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _render_labels(labels: _Labels, extra: str = "") -> str:
    parts = [f'{k}="{v.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for k, v in labels]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[_Labels, float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, dict[_Labels, float]] = defaultdict(dict)
        self._histograms: dict[str, dict[_Labels, list[float]]] = defaultdict(dict)
        self._help: dict[str, str] = {}

    def describe(self, name: str, text: str) -> None:
        self._help[name] = text

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[name][_labels(**labels)] += value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[name][_labels(**labels)] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            # [bucket counts..., +Inf count, sum]
            series = self._histograms[name].setdefault(_labels(**labels), [0.0] * (len(_BUCKETS) + 2))
            for index, bound in enumerate(_BUCKETS):
                if value <= bound:
                    series[index] += 1
            series[len(_BUCKETS)] += 1
            series[len(_BUCKETS) + 1] += value

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._counters.items()):
                lines += [f"# HELP {name} {self._help.get(name, name)}", f"# TYPE {name} counter"]
                lines += [f"{name}{_render_labels(l)} {v:g}" for l, v in sorted(series.items())]
            for name, series in sorted(self._gauges.items()):
                lines += [f"# HELP {name} {self._help.get(name, name)}", f"# TYPE {name} gauge"]
                lines += [f"{name}{_render_labels(l)} {v:g}" for l, v in sorted(series.items())]
            for name, series in sorted(self._histograms.items()):
                lines += [f"# HELP {name} {self._help.get(name, name)}", f"# TYPE {name} histogram"]
                for labels, data in sorted(series.items()):
                    for index, bound in enumerate(_BUCKETS):
                        lines.append(f"{name}_bucket{_render_labels(labels, f'le={chr(34)}{bound:g}{chr(34)}')} {data[index]:g}")
                    lines.append(f"{name}_bucket{_render_labels(labels, 'le=' + chr(34) + '+Inf' + chr(34))} {data[len(_BUCKETS)]:g}")
                    lines.append(f"{name}_sum{_render_labels(labels)} {data[len(_BUCKETS) + 1]:g}")
                    lines.append(f"{name}_count{_render_labels(labels)} {data[len(_BUCKETS)]:g}")
        return "\n".join(lines) + "\n"


metrics = _Registry()
metrics.describe("gateway_tool_calls_total", "Tool calls routed by the gateway, by server, tool and outcome")
metrics.describe("gateway_tool_call_duration_seconds", "Tool call latency in seconds, by server")
metrics.describe("gateway_upstream_up", "1 if the last probe of the upstream succeeded, else 0")
metrics.describe("gateway_http_requests_total", "HTTP requests to the admin API, by method and status class")
metrics.describe("gateway_rate_limited_total", "Requests rejected by a rate limiter, by scope")
metrics.describe("gateway_auth_failures_total", "Failed authentications, by kind")
