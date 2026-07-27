$(function () {
    function FilamentGuardViewModel(parameters) {
        var self = this;

        self.settingsViewModel = parameters[0];
        self.settings = undefined; // filled in onBeforeBinding

        self.sensorPresent = ko.observable(false);
        self.armed = ko.observable(false);
        self.inGrace = ko.observable(false);
        self.pulses = ko.observable(0);
        self.rate = ko.observable(0);
        self.commandedMm = ko.observable(0);
        self.lastRatio = ko.observable(null);
        self.calibrating = ko.observable(false);
        self.calibrationStatus = ko.observable("");

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

        self.onBeforeBinding = function () {
            self.settings = self.settingsViewModel.settings.plugins.filamentguard;
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
                self.pulses(data.pulses);
                self.rate(data.rate);
                self.commandedMm(data.commanded_mm);
                self.lastRatio(data.last_ratio);
                self.calibrating(data.calibrating);
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
