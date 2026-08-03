import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "octoprint_filamentguard"),
)

from detector import (  # noqa: E402
    ExtrusionTracker,
    JamDetector,
    MODE_DISTANCE,
    MODE_TIMEOUT,
    TRIGGER_NO_MOTION,
    TRIGGER_UNDER_EXTRUSION,
)


class TestExtrusionTracker:
    def test_absolute_e(self):
        t = ExtrusionTracker()
        assert t.process("G92 E0") == 0.0
        assert t.process("G1 X10 Y10 E5 F1800") == 5.0
        assert t.process("G1 X20 E7.5") == 2.5

    def test_relative_e_m83(self):
        t = ExtrusionTracker()
        t.process("M83")
        assert t.process("G1 E2.0") == 2.0
        assert t.process("G1 E3.0") == 3.0

    def test_g91_sets_relative_g90_absolute(self):
        t = ExtrusionTracker()
        t.process("G91")
        assert t.process("G1 E1.5") == 1.5
        t.process("G90")
        t.process("G92 E0")
        assert t.process("G1 E4") == 4.0

    def test_m82_overrides_g91(self):
        t = ExtrusionTracker()
        t.process("G91")
        t.process("M82")
        t.process("G92 E10")
        assert t.process("G1 E12") == 2.0

    def test_retraction_ignored(self):
        t = ExtrusionTracker()
        t.process("M83")
        assert t.process("G1 E-4 F3000") == 0.0
        assert t.process("G1 E4 F3000") == 4.0

    def test_g92_reset_mid_print(self):
        t = ExtrusionTracker()
        t.process("G92 E0")
        assert t.process("G1 E100") == 100.0
        t.process("G92 E0")
        assert t.process("G1 E1") == 1.0

    def test_comments_and_noise(self):
        t = ExtrusionTracker()
        assert t.process("; just a comment") == 0.0
        assert t.process("M104 S200") == 0.0
        assert t.process("G28") == 0.0
        assert t.process("") == 0.0

    def test_moves_without_e(self):
        t = ExtrusionTracker()
        t.process("G92 E0")
        assert t.process("G1 X50 Y50 F6000") == 0.0
        assert t.process("G1 E2") == 2.0


def feed(det, mm, pulses_fn, step=0.5):
    """Feed `mm` of extrusion in `step` chunks; pulses_fn(total_mm) -> pulse count."""
    total = 0.0
    fed = 0.0
    while fed < mm:
        d = min(step, mm - fed)
        fed += d
        total += d
        r = det.on_extrusion(d, pulses_fn(total))
        if r:
            return r, fed
    return None, fed


class TestJamDetectorTimeout:
    def make(self, **kw):
        kw.setdefault("mode", MODE_TIMEOUT)
        kw.setdefault("timeout_mm", 7.0)
        kw.setdefault("grace_mm", 10.0)
        d = JamDetector(**kw)
        d.arm(0)
        return d

    def test_normal_flow_no_trigger(self):
        d = self.make()
        # 1 pulse per mm
        r, _ = feed(d, 100, lambda mm: int(mm))
        assert r is None

    def test_full_jam_triggers(self):
        d = self.make()
        # pulses stop after 30mm total
        r, fed = feed(d, 60, lambda mm: min(int(mm), 30))
        assert r == TRIGGER_NO_MOTION
        # grace 10 + ~30mm of pulses then ~7mm timeout
        assert 35 < fed < 50

    def test_grace_swallows_startup(self):
        d = self.make()
        # no pulses at all: trigger should come at grace + timeout, not timeout
        r, fed = feed(d, 60, lambda mm: 0)
        assert r == TRIGGER_NO_MOTION
        assert 16 <= fed <= 19

    def test_disarm_stops_detection(self):
        d = self.make()
        d.disarm()
        r, _ = feed(d, 60, lambda mm: 0)
        assert r is None

    def test_timeout_floored_at_four_pulse_distances(self):
        # coarse sensor: 2.857mm/pulse, configured timeout tighter than 4 pulses
        d = self.make(timeout_mm=7.0, mm_per_pulse=2.857, grace_mm=0.0)
        assert abs(d.effective_timeout_mm - 4 * 2.857) < 1e-9
        r, _ = feed(d, 11, lambda mm: 0)
        assert r is None  # 7mm alone must not trigger
        r, _ = feed(d, 2, lambda mm: 0)
        assert r == TRIGGER_NO_MOTION  # past 11.4mm it must

    def test_sparse_pulses_at_pulse_distance_never_trigger(self):
        d = self.make(timeout_mm=7.0, mm_per_pulse=2.857, grace_mm=0.0)
        r, _ = feed(d, 200, lambda mm: int(mm / 2.857))
        assert r is None

    def test_explicit_timeout_above_floor_wins(self):
        d = self.make(timeout_mm=20.0, mm_per_pulse=2.857, grace_mm=0.0)
        assert d.effective_timeout_mm == 20.0

    def test_rearm_resets_state(self):
        d = self.make()
        feed(d, 16, lambda mm: 0)  # near trigger
        d.arm(0)
        r, _ = feed(d, 15, lambda mm: int(mm))
        assert r is None


class TestJamDetectorDistance:
    def make(self, **kw):
        kw.setdefault("mode", MODE_DISTANCE)
        kw.setdefault("mm_per_pulse", 1.0)
        kw.setdefault("timeout_mm", 7.0)
        kw.setdefault("window_mm", 20.0)
        kw.setdefault("ratio_threshold", 0.7)
        kw.setdefault("consecutive_windows", 2)
        kw.setdefault("grace_mm", 0.0)
        d = JamDetector(**kw)
        d.arm(0)
        return d

    def test_healthy_flow(self):
        d = self.make()
        r, _ = feed(d, 200, lambda mm: int(mm))
        assert r is None

    def test_partial_clog_triggers(self):
        d = self.make()
        # only 40% of filament actually moves
        r, fed = feed(d, 200, lambda mm: int(mm * 0.4))
        assert r == TRIGGER_UNDER_EXTRUSION
        assert fed <= 60  # two 20mm windows plus slack

    def test_single_bad_window_recovers(self):
        d = self.make()
        # 50% flow for first 20mm window only, then healthy
        def pulses(mm):
            if mm <= 20:
                return int(mm * 0.5)
            return 10 + int(mm - 20)

        r, _ = feed(d, 200, pulses)
        assert r is None

    def test_no_mm_per_pulse_skips_distance(self):
        d = self.make(mm_per_pulse=0.0)
        # 40% flow, but pulses still arrive -> no timeout, no distance check
        r, _ = feed(d, 200, lambda mm: int(mm * 0.4))
        assert r is None

    def test_full_jam_still_caught_by_timeout(self):
        d = self.make()
        r, fed = feed(d, 60, lambda mm: min(int(mm), 10))
        assert r == TRIGGER_NO_MOTION
