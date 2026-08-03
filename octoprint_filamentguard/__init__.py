# coding=utf-8
from __future__ import absolute_import

import threading
import time

import octoprint.plugin
from octoprint.events import Events
from octoprint.util import RepeatedTimer

from .detector import (
    ExtrusionTracker,
    JamDetector,
    MODE_DISTANCE,
    TRIGGER_NO_MOTION,
)


class FilamentGuardPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.ShutdownPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.SimpleApiPlugin,
):
    def __init__(self):
        self._sensor = None
        self._tracker = ExtrusionTracker()
        self._detector = JamDetector()
        self._lock = threading.Lock()
        self._status_timer = None
        self._calibrating = False
        self._last_count = 0
        self._last_rate_ts = time.monotonic()
        self._pulse_rate = 0.0
        self._debug_timer = None
        self._debug_until = 0.0
        self._arm_seq = 0
        self._confirm_pending = False

    # -- settings

    def get_settings_defaults(self):
        return dict(
            gpio_pin=-1,
            edge="rising",
            bias="pull_up",
            debounce_ms=2,
            mode="timeout",
            mm_per_pulse=0.0,
            timeout_mm=15.0,
            confirm_seconds=3.0,
            window_mm=20.0,
            ratio_threshold=0.7,
            consecutive_windows=2,
            grace_mm=10.0,
            action_pause=True,
            pause_command="",
            action_gcode="",
            popup=True,
            min_extrude_temp=180,
            calibration_length=100.0,
            calibration_feedrate=100,
        )

    def on_settings_save(self, data):
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        self._apply_settings()

    # -- lifecycle

    def on_after_startup(self):
        self._apply_settings()
        self._status_timer = RepeatedTimer(1.0, self._push_status)
        self._status_timer.start()

    def on_shutdown(self):
        if self._status_timer:
            self._status_timer.cancel()
        self._stop_debug()
        self._stop_sensor()

    def _apply_settings(self):
        s = self._settings
        with self._lock:
            was_armed = self._detector.armed
            self._detector = JamDetector(
                mode=s.get(["mode"]),
                mm_per_pulse=s.get_float(["mm_per_pulse"]),
                timeout_mm=s.get_float(["timeout_mm"]),
                window_mm=s.get_float(["window_mm"]),
                ratio_threshold=s.get_float(["ratio_threshold"]),
                consecutive_windows=s.get_int(["consecutive_windows"]),
                grace_mm=s.get_float(["grace_mm"]),
            )
        self._stop_sensor()
        pin = s.get_int(["gpio_pin"])
        if pin is None or pin < 0:
            self._logger.info("No GPIO pin configured, sensor disabled")
            return
        from .sensor import PulseSensor

        try:
            self._sensor = PulseSensor(
                pin=pin,
                edge=s.get(["edge"]),
                bias=s.get(["bias"]),
                debounce_ms=s.get_int(["debounce_ms"]),
                logger=self._logger,
            )
            self._sensor.start()
        except Exception:
            self._logger.exception("Failed to start pulse sensor on GPIO%d", pin)
            self._sensor = None
        if was_armed and self._sensor:
            self._arm(reason="settings changed mid-print")

    def _stop_sensor(self):
        if self._sensor:
            try:
                self._sensor.stop()
            except Exception:
                self._logger.exception("Failed to stop sensor")
            self._sensor = None

    # -- events / arming

    def _arm(self, reason):
        with self._lock:
            self._arm_seq += 1
            self._confirm_pending = False
            self._detector.arm(self._sensor.count)
        self._logger.info("FilamentGuard armed (%s)", reason)

    def on_event(self, event, payload):
        if event in (Events.PRINT_STARTED, Events.PRINT_RESUMED):
            if self._sensor:
                if event == Events.PRINT_STARTED:
                    # fresh job: reset E tracking (start G-code will G92 E0).
                    # On resume the tracker MUST survive — the job continues
                    # with absolute E values, and a reset would count the first
                    # move's whole E coordinate as one giant extrusion.
                    with self._lock:
                        self._tracker = ExtrusionTracker()
                self._arm(reason=event)
            else:
                self._logger.warning(
                    "Print started but no sensor configured — FilamentGuard inactive"
                )
        elif event in (
            Events.PRINT_PAUSED,
            Events.PRINT_DONE,
            Events.PRINT_FAILED,
            Events.PRINT_CANCELLED,
        ):
            with self._lock:
                self._detector.disarm()

    # -- gcode sent hook

    def on_gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode,
                      *args, **kwargs):
        if not gcode or not self._sensor:
            return
        confirm_args = None
        with self._lock:
            if not self._detector.armed:
                # keep E-mode/position tracking warm even when disarmed
                self._tracker.process(cmd)
                return
            delta = self._tracker.process(cmd)
            if delta <= 0:
                return
            trigger = self._detector.on_extrusion(delta, self._sensor.count)
            if trigger == TRIGGER_NO_MOTION:
                # sent commands run ahead of physical execution, so re-check
                # the pulse count after a delay instead of firing immediately
                if self._confirm_pending:
                    return
                self._confirm_pending = True
                confirm_args = (self._sensor.count, self._arm_seq)
            elif trigger:
                self._detector.disarm()
        if confirm_args:
            threading.Thread(
                target=self._confirm_no_motion, args=confirm_args, daemon=True
            ).start()
        elif trigger:
            threading.Thread(
                target=self._handle_trigger, args=(trigger,), daemon=True
            ).start()

    def _confirm_no_motion(self, count_snapshot, arm_seq):
        delay = self._settings.get_float(["confirm_seconds"])
        if delay > 0:
            time.sleep(delay)
        fire = False
        try:
            sensor = self._sensor
            if not sensor:
                return
            with self._lock:
                if not self._detector.armed or self._arm_seq != arm_seq:
                    return
                if sensor.count != count_snapshot:
                    self._logger.info(
                        "No-motion threshold crossed but pulses arrived within "
                        "%.1fs confirmation window — suppressed (printer "
                        "lagging behind sent commands)", delay,
                    )
                    return
                self._detector.disarm()
                fire = True
        finally:
            self._confirm_pending = False
        if fire:
            self._handle_trigger(TRIGGER_NO_MOTION)

    def _handle_trigger(self, trigger):
        reason = (
            "no filament motion detected"
            if trigger == TRIGGER_NO_MOTION
            else "under-extrusion detected (partial clog?)"
        )
        self._logger.warning("FilamentGuard triggered: %s", reason)
        payload = dict(trigger=trigger, reason=reason)
        self._event_bus.fire(Events.PLUGIN_FILAMENTGUARD_JAM, payload)

        s = self._settings
        gcode_script = (s.get(["action_gcode"]) or "").strip()
        if gcode_script:
            self._printer.commands(
                [l.strip() for l in gcode_script.splitlines() if l.strip()]
            )
        if s.get_boolean(["action_pause"]):
            pause_command = (s.get(["pause_command"]) or "").strip()
            if pause_command:
                self._printer.commands(pause_command)
            else:
                self._printer.pause_print()
        if s.get_boolean(["popup"]):
            self._plugin_manager.send_plugin_message(
                self._identifier, dict(type="jam", trigger=trigger, reason=reason)
            )

    # -- status push

    def _push_status(self):
        count = self._sensor.count if self._sensor else 0
        now = time.monotonic()
        dt = now - self._last_rate_ts
        if dt > 0:
            self._pulse_rate = (count - self._last_count) / dt
        self._last_count = count
        self._last_rate_ts = now
        self._plugin_manager.send_plugin_message(
            self._identifier,
            dict(
                type="status",
                sensor=self._sensor is not None,
                armed=self._detector.armed,
                in_grace=self._detector.in_grace,
                pin=self._sensor.pin if self._sensor else None,
                level=self._sensor.level if self._sensor else None,
                pulses=count,
                rate=round(self._pulse_rate, 1),
                commanded_mm=round(self._detector.commanded_total, 1),
                last_ratio=self._detector.last_ratio,
                mm_per_pulse=self._settings.get_float(["mm_per_pulse"]),
                calibrating=self._calibrating,
            ),
        )

    # -- pin debug stream

    DEBUG_INTERVAL = 0.2
    DEBUG_AUTO_OFF = 600  # seconds

    def _start_debug(self):
        self._debug_until = time.monotonic() + self.DEBUG_AUTO_OFF
        if self._debug_timer is None:
            self._debug_timer = RepeatedTimer(
                self.DEBUG_INTERVAL,
                self._push_debug,
                condition=self._debug_active,
                on_condition_false=self._on_debug_stopped,
                run_first=True,
            )
            self._debug_timer.start()
        self._push_debug_state(True)

    def _stop_debug(self):
        self._debug_until = 0.0
        if self._debug_timer:
            self._debug_timer.cancel()
            self._debug_timer = None
            self._push_debug_state(False)

    def _debug_active(self):
        return self._sensor is not None and time.monotonic() < self._debug_until

    def _on_debug_stopped(self):
        self._debug_timer = None
        self._push_debug_state(False)

    def _push_debug_state(self, enabled):
        self._plugin_manager.send_plugin_message(
            self._identifier, dict(type="debug_state", enabled=enabled)
        )

    def _push_debug(self):
        sensor = self._sensor
        if not sensor:
            return
        self._plugin_manager.send_plugin_message(
            self._identifier,
            dict(type="debug", level=sensor.level, pulses=sensor.count),
        )

    # -- simple api

    def get_api_commands(self):
        return dict(calibrate=[], debug=["enabled"])

    def on_api_command(self, command, data):
        import flask

        if command == "calibrate":
            if not octoprint.access.permissions.Permissions.CONTROL.can():
                return flask.abort(403)
            error = self._start_calibration()
            if error:
                return flask.jsonify(dict(ok=False, error=error))
            return flask.jsonify(dict(ok=True))
        if command == "debug":
            if not octoprint.access.permissions.Permissions.STATUS.can():
                return flask.abort(403)
            if data.get("enabled"):
                if not self._sensor:
                    return flask.jsonify(
                        dict(ok=False, error="No sensor configured")
                    )
                self._start_debug()
            else:
                self._stop_debug()
            return flask.jsonify(dict(ok=True))

    def _start_calibration(self):
        if self._calibrating:
            return "Calibration already running"
        if not self._sensor:
            return "No sensor configured (set a GPIO pin first)"
        if not self._printer.is_operational() or self._printer.is_printing():
            return "Printer must be operational and idle"
        temps = self._printer.get_current_temperatures()
        tool0 = temps.get("tool0", {}).get("actual") or 0
        min_temp = self._settings.get_int(["min_extrude_temp"])
        if tool0 < min_temp:
            return "Hotend too cold (%d°C < %d°C) — heat it first" % (tool0, min_temp)
        self._calibrating = True
        threading.Thread(target=self._run_calibration, daemon=True).start()
        return None

    def _run_calibration(self):
        try:
            length = self._settings.get_float(["calibration_length"])
            feed = self._settings.get_int(["calibration_feedrate"])
            start = self._sensor.count
            self._logger.info(
                "Calibration: extruding %.0fmm at F%d", length, feed
            )
            self._plugin_manager.send_plugin_message(
                self._identifier,
                dict(type="calibration", state="running", length=length),
            )
            self._printer.commands(["M83", "G1 E%.1f F%d" % (length, feed), "M82"])
            time.sleep(length / feed * 60 + 5)
            pulses = self._sensor.count - start
            if pulses < 10:
                self._logger.warning(
                    "Calibration failed: only %d pulses counted", pulses
                )
                self._plugin_manager.send_plugin_message(
                    self._identifier,
                    dict(type="calibration", state="failed", pulses=pulses),
                )
                return
            mm_per_pulse = length / pulses
            self._settings.set_float(["mm_per_pulse"], mm_per_pulse)
            self._settings.save()
            self._detector.mm_per_pulse = mm_per_pulse
            self._logger.info(
                "Calibration done: %d pulses over %.0fmm -> %.4f mm/pulse",
                pulses, length, mm_per_pulse,
            )
            self._event_bus.fire(
                Events.PLUGIN_FILAMENTGUARD_CALIBRATION_DONE,
                dict(pulses=pulses, mm_per_pulse=mm_per_pulse),
            )
            self._plugin_manager.send_plugin_message(
                self._identifier,
                dict(
                    type="calibration",
                    state="done",
                    pulses=pulses,
                    mm_per_pulse=round(mm_per_pulse, 4),
                ),
            )
        finally:
            self._calibrating = False

    # -- ui

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=True),
            dict(type="sidebar", name="Filament Guard", icon="road"),
        ]

    def get_assets(self):
        return dict(js=["js/filamentguard.js"])

    # -- hooks

    def register_custom_events(*args, **kwargs):
        return ["jam", "calibration_done"]

    def get_update_information(self):
        return dict(
            filamentguard=dict(
                displayName="FilamentGuard",
                displayVersion=self._plugin_version,
                type="github_release",
                user="ionutporoh",
                repo="OctoPrint-FilamentGuard",
                current=self._plugin_version,
                pip="https://github.com/ionutporoh/OctoPrint-FilamentGuard/archive/{target_version}.zip",
            )
        )


__plugin_name__ = "FilamentGuard"
__plugin_pythoncompat__ = ">=3.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = FilamentGuardPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.gcode.sent":
            __plugin_implementation__.on_gcode_sent,
        "octoprint.events.register_custom_events":
            __plugin_implementation__.register_custom_events,
        "octoprint.plugin.softwareupdate.check_config":
            __plugin_implementation__.get_update_information,
    }
