"""Pure detection logic for FilamentGuard.

No OctoPrint or GPIO dependencies so it can be unit-tested anywhere.
All distances are millimetres of filament at the extruder.
"""

MODE_TIMEOUT = "timeout"
MODE_DISTANCE = "distance"

TRIGGER_NO_MOTION = "no_motion"
TRIGGER_UNDER_EXTRUSION = "under_extrusion"


def _words(line):
    line = line.split(";", 1)[0].strip()
    if not line:
        return None, []
    parts = line.split()
    return parts[0].upper(), parts[1:]


class ExtrusionTracker:
    """Tracks net commanded extrusion from a stream of sent G-code commands.

    Only positive E deltas are reported; retractions are ignored (the sensor
    pulses on movement in both directions, so extra pulses from
    retract/deretract can only make detection more conservative, never a
    false positive).
    """

    def __init__(self):
        self.e_relative = False
        self.last_e = 0.0

    def process(self, line):
        """Returns positive extrusion delta in mm for this command (0.0 if none)."""
        cmd, args = _words(line)
        if cmd is None:
            return 0.0

        if cmd in ("G90", "M82"):
            self.e_relative = False
            return 0.0
        if cmd in ("G91", "M83"):
            self.e_relative = True
            return 0.0

        if cmd == "G92":
            for a in args:
                if a[0] in "Ee":
                    try:
                        self.last_e = float(a[1:])
                    except ValueError:
                        pass
            return 0.0

        if cmd in ("G0", "G1", "G2", "G3"):
            for a in args:
                if a[0] in "Ee":
                    try:
                        e = float(a[1:])
                    except ValueError:
                        return 0.0
                    if self.e_relative:
                        delta = e
                    else:
                        delta = e - self.last_e
                        self.last_e = e
                    return delta if delta > 0 else 0.0
        return 0.0


class JamDetector:
    """Distance-driven jam/clog detection.

    Fed with (extrusion delta, absolute pulse count) pairs while armed.
    ``timeout`` mode: trigger when ``timeout_mm`` of commanded extrusion pass
    with zero pulses. ``distance`` mode additionally compares measured vs
    commanded filament over ``window_mm`` windows to catch partial clogs
    (requires ``mm_per_pulse`` from calibration).
    """

    def __init__(self, mode=MODE_TIMEOUT, mm_per_pulse=0.0, timeout_mm=7.0,
                 window_mm=20.0, ratio_threshold=0.7, consecutive_windows=2,
                 grace_mm=10.0):
        self.mode = mode
        self.mm_per_pulse = mm_per_pulse
        self.timeout_mm = timeout_mm
        self.window_mm = window_mm
        self.ratio_threshold = ratio_threshold
        self.consecutive_windows = consecutive_windows
        self.grace_mm = grace_mm

        self.armed = False
        self.in_grace = False
        self.last_ratio = None
        self.commanded_total = 0.0
        self._grace_left = 0.0
        self._mm_since_pulse = 0.0
        self._last_pulses = 0
        self._window_mm_acc = 0.0
        self._window_pulse_start = 0
        self._bad_windows = 0

    def arm(self, pulse_count):
        self.armed = True
        self.in_grace = self.grace_mm > 0
        self.last_ratio = None
        self.commanded_total = 0.0
        self._grace_left = self.grace_mm
        self._mm_since_pulse = 0.0
        self._last_pulses = pulse_count
        self._window_mm_acc = 0.0
        self._window_pulse_start = pulse_count
        self._bad_windows = 0

    def disarm(self):
        self.armed = False
        self.in_grace = False

    def on_extrusion(self, delta_mm, pulse_count):
        """Returns TRIGGER_NO_MOTION / TRIGGER_UNDER_EXTRUSION or None."""
        if not self.armed or delta_mm <= 0:
            return None

        if self.in_grace:
            self._grace_left -= delta_mm
            if self._grace_left > 0:
                return None
            # grace over: reset baselines so priming noise doesn't count
            self.in_grace = False
            delta_mm = -self._grace_left  # extrusion past the grace boundary
            self._last_pulses = pulse_count
            self._window_pulse_start = pulse_count
            self._mm_since_pulse = 0.0
            self._window_mm_acc = 0.0
            if delta_mm <= 0:
                return None

        self.commanded_total += delta_mm

        if pulse_count != self._last_pulses:
            self._last_pulses = pulse_count
            self._mm_since_pulse = 0.0
        else:
            self._mm_since_pulse += delta_mm
        if self._mm_since_pulse >= self.timeout_mm:
            return TRIGGER_NO_MOTION

        if self.mode == MODE_DISTANCE and self.mm_per_pulse > 0:
            self._window_mm_acc += delta_mm
            if self._window_mm_acc >= self.window_mm:
                measured = (pulse_count - self._window_pulse_start) * self.mm_per_pulse
                self.last_ratio = measured / self._window_mm_acc
                if self.last_ratio < self.ratio_threshold:
                    self._bad_windows += 1
                else:
                    self._bad_windows = 0
                self._window_mm_acc = 0.0
                self._window_pulse_start = pulse_count
                if self._bad_windows >= self.consecutive_windows:
                    return TRIGGER_UNDER_EXTRUSION
        return None
