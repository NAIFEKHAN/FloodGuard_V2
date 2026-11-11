(() => {
  "use strict";

  function classifyMoisture(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) {
      return null;
    }
    if (value < 60) return "normal";
    if (value < 80) return "elevated";
    return "high";
  }

  function evaluateCurrentMoisture(status, value) {
    return status === "online" ? classifyMoisture(value) : null;
  }

  const alerts = Object.freeze({ classifyMoisture, evaluateCurrentMoisture });
  if (typeof module !== "undefined" && module.exports) {
    module.exports = alerts;
  } else {
    window.FloodGuardSoilAlerts = alerts;
  }
})();
