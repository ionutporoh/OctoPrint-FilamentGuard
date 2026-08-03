$(function () {
    function FilamentGuardViewModel(parameters) {
        var self = this;

        self.settingsViewModel = parameters[0];
        self.settings = undefined; // filled in onBeforeBinding

        self.sensorPresent = ko.observable(false);
        self.armed = ko.observable(false);
        self.inGrace = ko.observable(false);
        self.pin = ko.observable(null);
        self.level = ko.observable(null);
        self.pulses = ko.observable(0);
        self.rate = ko.observable(0);
        self.commandedMm = ko.observable(0);
        self.lastRatio = ko.observable(null);
        self.mmPerPulse = ko.observable(0);
        self.calibrating = ko.observable(false);
        self.calibrationStatus = ko.observable("");
        self.debugEnabled = ko.observable(false);
        self._debugSamples = [];

        self.stateText = ko.pureComputed(function () {
            if (!self.sensorPresent()) return gettext("No sensor");
            if (self.calibrating()) return gettext("Calibrating…");
            if (self.armed())
                return self.inGrace() ? gettext("Armed (grace)") : gettext("Armed");
            return gettext("Idle");
        });
        self.stateClass = ko.pureComputed(function () {
            if (!self.sensorPresent()) return "text-error";
            return self.armed() ? "text-success" : "muted";
        });
        self.ratioText = ko.pureComputed(function () {
            var r = self.lastRatio();
            return r === null ? "–" : (r * 100).toFixed(0) + "%";
        });
        self.levelText = ko.pureComputed(function () {
            var l = self.level();
            return l === null ? "–" : l ? "HIGH" : "LOW";
        });
        self.levelClass = ko.pureComputed(function () {
            var l = self.level();
            return l === null ? "" : l ? "label-success" : "label-inverse";
        });
        self.positionMm = ko.pureComputed(function () {
            var mmpp = self.mmPerPulse();
            if (!mmpp) return null;
            return (self.pulses() * mmpp).toFixed(1);
        });

        self.onBeforeBinding = function () {
            self.settings = self.settingsViewModel.settings.plugins.filamentguard;
        };

        self.toggleDebug = function () {
            OctoPrint.simpleApiCommand("filamentguard", "debug", {
                enabled: !self.debugEnabled()
            });
        };

        self._drawWave = function () {
            var canvas = document.getElementById("filamentguard_wave");
            if (!canvas || !canvas.getContext) return;
            var ctx = canvas.getContext("2d");
            var w = canvas.width;
            var h = canvas.height;
            ctx.clearRect(0, 0, w, h);
            var samples = self._debugSamples;
            var n = samples.length;
            if (n < 2) return;
            var maxSamples = 150; // 30 s at 5 Hz
            var dx = w / maxSamples;
            var yHigh = 6;
            var yLow = h - 12;
            var yFor = function (s) {
                return s.level ? yHigh : yLow;
            };
            ctx.strokeStyle = "#3a87ad";
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(w - n * dx, yFor(samples[0]));
            for (var i = 1; i < n; i++) {
                var x = w - (n - i) * dx;
                ctx.lineTo(x, yFor(samples[i - 1]));
                ctx.lineTo(x, yFor(samples[i]));
            }
            ctx.lineTo(w, yFor(samples[n - 1]));
            ctx.stroke();
            ctx.strokeStyle = "#b94a48";
            ctx.lineWidth = 1;
            for (var j = 1; j < n; j++) {
                if (samples[j].pulses > samples[j - 1].pulses) {
                    var xt = w - (n - j) * dx;
                    ctx.beginPath();
                    ctx.moveTo(xt, h - 8);
                    ctx.lineTo(xt, h);
                    ctx.stroke();
                }
            }
        };

        self.calibrate = function () {
            self.calibrationStatus("");
            OctoPrint.simpleApiCommand("filamentguard", "calibrate").done(function (response) {
                if (response && response.ok === false) {
                    self.calibrationStatus(response.error);
                }
            });
        };

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin !== "filamentguard") return;

            if (data.type === "status") {
                self.sensorPresent(data.sensor);
                self.armed(data.armed);
                self.inGrace(data.in_grace);
                self.pin(data.pin);
                if (!self.debugEnabled()) self.level(data.level);
                self.pulses(data.pulses);
                self.rate(data.rate);
                self.commandedMm(data.commanded_mm);
                self.lastRatio(data.last_ratio);
                self.mmPerPulse(data.mm_per_pulse);
                self.calibrating(data.calibrating);
            } else if (data.type === "debug") {
                self.debugEnabled(true);
                self.level(data.level);
                self._debugSamples.push({level: data.level, pulses: data.pulses});
                if (self._debugSamples.length > 150) self._debugSamples.shift();
                self._drawWave();
            } else if (data.type === "debug_state") {
                self.debugEnabled(data.enabled);
                if (!data.enabled) self._debugSamples = [];
            } else if (data.type === "calibration") {
                if (data.state === "running") {
                    self.calibrationStatus(gettext("Extruding…"));
                } else if (data.state === "done") {
                    self.calibrationStatus(
                        _.sprintf(gettext("Done: %(pulses)d pulses → %(mmpp)s mm/pulse"), {
                            pulses: data.pulses,
                            mmpp: data.mm_per_pulse
                        })
                    );
                    self.settings.mm_per_pulse(data.mm_per_pulse);
                } else if (data.state === "failed") {
                    self.calibrationStatus(
                        _.sprintf(gettext("Failed: only %(pulses)d pulses counted"), data)
                    );
                }
            } else if (data.type === "jam") {
                new PNotify({
                    title: gettext("Filament Guard"),
                    text: data.reason,
                    type: "error",
                    hide: false
                });
            }
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: FilamentGuardViewModel,
        dependencies: ["settingsViewModel"],
        elements: ["#settings_plugin_filamentguard", "#sidebar_plugin_filamentguard"]
    });
});
