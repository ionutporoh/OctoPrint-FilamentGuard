#!/usr/bin/env python3
"""Find which GPIO the filament sensor pulses on.

Run on the Pi (inside the OctoPrint venv, which has gpiod):

    sudo -u john /home/john/oprint/bin/python3 pin_scan.py

Then slowly pull filament through the sensor by hand. The pin whose count
climbs in step with filament movement is your sensor pin. Ctrl-C to stop.

Pins already claimed by other software (relays etc.) are skipped
automatically. Floating pins may show random noise — trust the pin that
only counts while you move filament.
"""

import time

import gpiod
from gpiod.line import Bias, Edge

CHIP = "/dev/gpiochip0"
CANDIDATES = range(2, 28)

requests = {}
for pin in CANDIDATES:
    try:
        requests[pin] = gpiod.request_lines(
            CHIP,
            consumer="fg-pin-scan",
            config={pin: gpiod.LineSettings(edge_detection=Edge.BOTH, bias=Bias.PULL_UP)},
        )
    except OSError as exc:
        print(f"GPIO{pin:2d}: skipped ({exc})")

if not requests:
    raise SystemExit("No GPIOs available to watch")

print(f"\nWatching {len(requests)} pins — feed filament through the sensor now.")
print("Ctrl-C to stop.\n")

counts = {pin: 0 for pin in requests}
last_print = time.monotonic()
try:
    while True:
        for pin, req in requests.items():
            while req.wait_edge_events(0):
                counts[pin] += len(req.read_edge_events())
        time.sleep(0.02)
        if time.monotonic() - last_print >= 2:
            active = {p: c for p, c in counts.items() if c}
            line = "  ".join(f"GPIO{p}={c}" for p, c in sorted(active.items()))
            print(line or "(no edges yet)")
            last_print = time.monotonic()
except KeyboardInterrupt:
    print("\n\n=== Summary (edges counted) ===")
    for pin, c in sorted(counts.items(), key=lambda x: -x[1]):
        if c:
            print(f"GPIO{pin:2d}: {c}")
    for req in requests.values():
        req.release()
