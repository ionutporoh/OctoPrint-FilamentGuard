---
layout: plugin

id: filamentguard
title: FilamentGuard
description: Filament jam and runout detection using a hall-effect pulse sensor, with no-motion and under-extrusion (partial clog) detection modes and built-in calibration.
authors:
- Ionut Poroh
license: AGPLv3

date: 2026-07-27

homepage: https://github.com/ionutporoh/OctoPrint-FilamentGuard
source: https://github.com/ionutporoh/OctoPrint-FilamentGuard
archive: https://github.com/ionutporoh/OctoPrint-FilamentGuard/archive/main.zip

tags:
- filament
- sensor
- jam
- runout
- clog
- gpio

compatibility:
  octoprint:
  - 1.5.0
  os:
  - linux
  python: ">=3.7,<4"
---

FilamentGuard watches a hall-effect pulse sensor (encoder style — pulses as
filament physically moves) and compares actual filament motion against the
extrusion your printer was commanded to perform.

**Detection modes**

- *No-motion timeout*: N mm of commanded extrusion with zero sensor pulses
  triggers a jam/runout (default 7 mm).
- *Distance comparison*: measured filament (pulses × mm-per-pulse) is
  compared against commanded extrusion over sliding windows — sustained low
  flow ratio catches partial clogs and heavy under-extrusion.

**Highlights**

- One-click calibration: extrudes a fixed length and learns your sensor's
  mm-per-pulse.
- Configurable trigger actions: pause (OctoPrint pause or `M600`), custom
  G-code, UI popup, and a custom `plugin_filamentguard_jam` event on the
  event bus (forwarded by e.g. OctoPrint-MQTT).
- Sidebar widget with live pulse count/rate and flow ratio.
- Grace distance after start/resume, correct absolute/relative E handling.
- libgpiod v2 kernel edge events with debounce — no RPi.GPIO.
