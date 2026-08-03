#!/usr/bin/env python3
"""Live GPIO monitor for FilamentGuard debugging.

Watches one or more pins and draws a scrolling waveform of the line level,
plus edge counters, pulse rate and filament position (if you pass your
calibrated mm/pulse). Run on the Pi (inside the OctoPrint venv, which has
gpiod) — stop OctoPrint first if it holds the pin, or watch a different one:

    sudo -u john /home/john/oprint/bin/python3 gpio_monitor.py 17
    sudo -u john /home/john/oprint/bin/python3 gpio_monitor.py 17 27 --mm-per-pulse 0.088

Waveform legend: ▔ high, ▁ low, ╳ edge(s) seen during that frame.
Ctrl-C stops and prints a summary. Optionally log every edge with
--log edges.csv for offline analysis.
"""

import argparse
import collections
import shutil
import sys
import time

import gpiod
from gpiod.line import Bias, Edge, Value
from gpiod.edge_event import EdgeEvent

BIAS_MAP = {"pull_up": Bias.PULL_UP, "pull_down": Bias.PULL_DOWN, "none": Bias.AS_IS}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pins", nargs="+", type=int, help="BCM GPIO number(s) to watch")
    p.add_argument("--chip", default="/dev/gpiochip0")
    p.add_argument("--bias", choices=BIAS_MAP, default="pull_up")
    p.add_argument("--count-edge", choices=["rising", "falling", "both"],
                   default="rising",
                   help="which edges count as pulses for rate/position "
                        "(match your plugin setting; default rising)")
    p.add_argument("--mm-per-pulse", type=float, default=0.0,
                   help="calibrated mm/pulse; enables the position readout")
    p.add_argument("--fps", type=float, default=25.0,
                   help="waveform frames per second (default 25)")
    p.add_argument("--log", metavar="FILE",
                   help="append every edge as CSV: time_s,pin,edge")
    return p.parse_args()


def main():
    args = parse_args()
    settings = gpiod.LineSettings(
        edge_detection=Edge.BOTH, bias=BIAS_MAP[args.bias]
    )
    try:
        request = gpiod.request_lines(
            args.chip,
            consumer="fg-gpio-monitor",
            config={pin: settings for pin in args.pins},
        )
    except OSError as exc:
        raise SystemExit(
            f"Cannot claim pin(s) {args.pins}: {exc}\n"
            "Hint: OctoPrint/FilamentGuard may hold the line — "
            "set the plugin pin to -1 or stop OctoPrint first."
        )

    logf = open(args.log, "a", buffering=1) if args.log else None
    if logf and logf.tell() == 0:
        logf.write("time_s,pin,edge\n")

    width = max(20, shutil.get_terminal_size().columns - 10)
    frame_s = 1.0 / args.fps
    start = time.monotonic()

    class PinState:
        def __init__(self):
            self.rising = 0
            self.falling = 0
            self.pulses = 0
            self.wave = collections.deque(maxlen=width)
            self.edge_in_frame = False
            self.rate_window = collections.deque()  # pulse timestamps, last 2 s

    pins = {pin: PinState() for pin in args.pins}
    count_rising = args.count_edge in ("rising", "both")
    count_falling = args.count_edge in ("falling", "both")

    lines_used = 0
    try:
        next_frame = time.monotonic()
        while True:
            # drain edge events
            while request.wait_edge_events(0):
                for ev in request.read_edge_events():
                    st = pins[ev.line_offset]
                    st.edge_in_frame = True
                    now = time.monotonic() - start
                    if ev.event_type == EdgeEvent.Type.RISING_EDGE:
                        st.rising += 1
                        edge = "rising"
                        if count_rising:
                            st.pulses += 1
                            st.rate_window.append(now)
                    else:
                        st.falling += 1
                        edge = "falling"
                        if count_falling:
                            st.pulses += 1
                            st.rate_window.append(now)
                    if logf:
                        logf.write(f"{now:.6f},{ev.line_offset},{edge}\n")

            now = time.monotonic()
            if now >= next_frame:
                next_frame = now + frame_s
                values = request.get_values()
                if lines_used:
                    sys.stdout.write(f"\x1b[{lines_used}F")  # cursor up, redraw
                lines_used = 0
                elapsed = now - start
                for pin, value in zip(request.offsets, values):
                    st = pins[pin]
                    level = 1 if value == Value.ACTIVE else 0
                    if st.edge_in_frame:
                        st.wave.append("╳")
                    else:
                        st.wave.append("▔" if level else "▁")
                    st.edge_in_frame = False
                    while st.rate_window and elapsed - st.rate_window[0] > 2.0:
                        st.rate_window.popleft()
                    rate = len(st.rate_window) / 2.0
                    status = "HIGH" if level else "LOW "
                    info = (
                        f"  edges: ↑{st.rising} ↓{st.falling}"
                        f"  pulses({args.count_edge}): {st.pulses}"
                        f"  rate: {rate:5.1f}/s"
                    )
                    if args.mm_per_pulse > 0:
                        info += f"  pos: {st.pulses * args.mm_per_pulse:8.2f} mm"
                    wave = "".join(st.wave).ljust(width)
                    sys.stdout.write(f"\x1b[2KGPIO{pin:<2d} [{status}] {wave}\n")
                    sys.stdout.write(f"\x1b[2K{info}\n")
                    lines_used += 2
                sys.stdout.write(f"\x1b[2K  t={elapsed:7.1f}s   Ctrl-C to stop\n")
                lines_used += 1
                sys.stdout.flush()
            time.sleep(min(frame_s / 4, 0.01))
    except KeyboardInterrupt:
        elapsed = time.monotonic() - start
        print("\n=== Summary ===")
        for pin, st in sorted(pins.items()):
            line = (
                f"GPIO{pin:2d}: ↑{st.rising} ↓{st.falling} edges, "
                f"{st.pulses} pulses ({args.count_edge}) in {elapsed:.1f}s"
            )
            if args.mm_per_pulse > 0:
                line += f" -> {st.pulses * args.mm_per_pulse:.2f} mm"
            print(line)
    finally:
        if logf:
            logf.close()
        request.release()


if __name__ == "__main__":
    main()
