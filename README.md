# OctoPrint-FilamentGuard

Filament jam / runout detection for OctoPrint using a hall-effect pulse
sensor (encoder-style: pulses as filament moves).

Two detection modes, selectable in settings:

- **No-motion timeout** — trigger when N mm of commanded extrusion pass with
  zero sensor pulses (full jam or runout).
- **Distance comparison** — additionally compares measured filament
  (pulses × mm-per-pulse) against commanded extrusion over sliding windows,
  catching partial clogs / heavy under-extrusion. Requires calibration
  (built in: extrudes 100 mm and learns mm-per-pulse).

On trigger: fires a custom OctoPrint event `plugin_filamentguard_jam`
(auto-published by OctoPrint-MQTT → Home Assistant), optionally runs a
G-code snippet, pauses the print (OctoPrint pause or `M600`), and shows a
UI popup. OctoPod pushes a notification on the resulting pause.

GPIO access uses libgpiod v2 (`gpiod` Python package) — no RPi.GPIO.

## Install

```sh
./deploy.sh            # rsync to the Pi + pip install -e + restart OctoPrint
```

Find the sensor pin: `scripts/pin_scan.py` (see its docstring), then set the
BCM pin in Settings → Filament Guard.

## Dev

```sh
pip install pytest && pytest tests/
```
