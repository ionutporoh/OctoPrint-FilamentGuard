# OctoPrint-FilamentGuard

Filament jam and runout detection for OctoPrint using a hall-effect **pulse**
sensor — the encoder style that emits pulses as filament physically moves
(BTT Smart Filament Sensor and similar, or any DIY hall/optical encoder).

Because it watches actual filament *motion* against what the printer was
*told* to extrude, it catches problems a simple runout switch can't:

- **Runout / full jam** — N mm of commanded extrusion with zero pulses
  (default 7 mm) triggers immediately.
- **Partial clogs / heavy under-extrusion** (optional *distance* mode) —
  measured filament (pulses × mm-per-pulse) is compared against commanded
  extrusion over sliding windows; sustained low flow ratio triggers.

## Features

- **Built-in calibration** — one click extrudes a fixed length and learns
  your sensor's mm-per-pulse. No datasheet needed.
- **Configurable actions** on trigger: pause the print (OctoPrint pause or
  `M600`), run a custom G-code snippet first (park, beep…), UI popup.
- **Custom event** `plugin_filamentguard_jam` fired on OctoPrint's event
  bus — automatically forwarded by OctoPrint-MQTT to Home Assistant etc.
- **Sidebar widget** with live pulse count, pulse rate and flow ratio.
- **Grace distance** after start/resume so priming lines don't false-trigger,
  correct handling of absolute/relative E (`M82`/`M83`/`G91`), `G92` resets,
  and retractions.
- Modern GPIO stack: **libgpiod v2** kernel edge events (no RPi.GPIO),
  kernel-level debounce with software fallback. Works on Python 3.7–3.13.

## Installation

Install via the OctoPrint Plugin Manager (⚙ → Plugin Manager → Get More →
"...from URL"):

    https://github.com/ionutporoh/OctoPrint-FilamentGuard/archive/main.zip

The `gpiod` dependency (>= 2.0) is installed automatically. A Linux host
with a GPIO character device (`/dev/gpiochip0`) is required — any
Raspberry Pi qualifies.

## Setup

1. Wire the sensor's pulse output to a free GPIO (3.3 V logic!). Open-drain
   outputs work with the plugin's internal pull-up.
2. Don't know which pin it's on? Run the bundled scanner on the Pi and pull
   filament through the sensor by hand:

       ~/oprint/bin/python3 scripts/pin_scan.py

3. Set the BCM pin in *Settings → Filament Guard*.
4. With the nozzle hot and the printer idle, click **Calibrate now** —
   the plugin extrudes 100 mm and stores mm-per-pulse (only needed for
   *distance* mode).
5. Pick a detection mode. `timeout` (no-motion) is a safe start; switch to
   `distance` after calibrating to also catch partial clogs.

## Settings reference

| Setting | Default | Meaning |
|---|---|---|
| Trigger after (mm) | 7 | Commanded extrusion with zero pulses → jam/runout |
| Comparison window | 20 mm | Distance-mode flow measurement window |
| Minimum flow ratio | 0.7 | Below this for N consecutive windows → clog |
| Grace distance | 10 mm | Ignored extrusion after print start/resume |
| Debounce | 2 ms | Pulse debounce (kernel, or software fallback) |

## Development

    pip install pytest
    pytest tests/          # detector logic is pure Python, no hardware needed

`deploy.sh` rsyncs the working tree to a Pi, pip-installs it editable into
the OctoPrint venv and restarts the service (adjust `HOST`/paths inside).

## License

AGPLv3 — see [LICENSE](LICENSE).
