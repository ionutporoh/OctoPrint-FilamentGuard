"""GPIO pulse reader for FilamentGuard.

Uses the libgpiod v2 Python bindings (``gpiod`` >= 2.x) with kernel edge
events. Debounce is requested from the kernel when supported; otherwise a
software debounce fallback is applied in the reader thread.
"""

import threading
import time
from datetime import timedelta

import gpiod
from gpiod.line import Bias, Edge, Value

EDGE_MAP = {"rising": Edge.RISING, "falling": Edge.FALLING, "both": Edge.BOTH}
BIAS_MAP = {"pull_up": Bias.PULL_UP, "pull_down": Bias.PULL_DOWN, "none": Bias.AS_IS}


class PulseSensor:
    def __init__(self, pin, edge="rising", bias="pull_up", debounce_ms=2,
                 chip_path="/dev/gpiochip0", logger=None):
        self.pin = int(pin)
        self.edge = edge
        self.bias = bias
        self.debounce_ms = int(debounce_ms)
        self.chip_path = chip_path
        self._logger = logger
        self._request = None
        self._thread = None
        self._running = False
        self._sw_debounce = False
        self._last_edge_ns = 0
        self._count = 0
        self.last_pulse_time = 0.0

    @property
    def count(self):
        return self._count

    @property
    def level(self):
        """Current line level (1/0), or None if unavailable."""
        request = self._request
        if not request:
            return None
        try:
            return 1 if request.get_value(self.pin) == Value.ACTIVE else 0
        except OSError:
            return None

    def start(self):
        settings = gpiod.LineSettings(
            edge_detection=EDGE_MAP[self.edge], bias=BIAS_MAP[self.bias]
        )
        if self.debounce_ms > 0:
            settings.debounce_period = timedelta(milliseconds=self.debounce_ms)
        try:
            self._request = gpiod.request_lines(
                self.chip_path, consumer="filamentguard", config={self.pin: settings}
            )
        except OSError:
            if self.debounce_ms > 0:
                # kernel may not support debounce on this line: software fallback
                settings.debounce_period = timedelta(0)
                self._request = gpiod.request_lines(
                    self.chip_path, consumer="filamentguard",
                    config={self.pin: settings},
                )
                self._sw_debounce = True
                if self._logger:
                    self._logger.info(
                        "Kernel debounce unavailable on GPIO%d, using software debounce",
                        self.pin,
                    )
            else:
                raise
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="filamentguard-sensor", daemon=True
        )
        self._thread.start()
        if self._logger:
            self._logger.info(
                "Pulse sensor started on GPIO%d (edge=%s bias=%s debounce=%dms)",
                self.pin, self.edge, self.bias, self.debounce_ms,
            )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._request:
            self._request.release()
            self._request = None

    def _loop(self):
        debounce_ns = self.debounce_ms * 1_000_000
        while self._running:
            try:
                if not self._request.wait_edge_events(0.5):
                    continue
                events = self._request.read_edge_events()
            except OSError:
                if self._running and self._logger:
                    self._logger.exception("GPIO read failed, sensor thread exiting")
                return
            if self._sw_debounce and debounce_ns:
                accepted = 0
                for ev in events:
                    if ev.timestamp_ns - self._last_edge_ns >= debounce_ns:
                        accepted += 1
                        self._last_edge_ns = ev.timestamp_ns
                self._count += accepted
                if accepted:
                    self.last_pulse_time = time.monotonic()
            elif events:
                self._count += len(events)
                self.last_pulse_time = time.monotonic()
