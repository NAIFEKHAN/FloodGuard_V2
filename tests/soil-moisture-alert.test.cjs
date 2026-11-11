const test = require("node:test");
const assert = require("node:assert/strict");

const {
  classifyMoisture,
  evaluateCurrentMoisture
} = require("../frontend/js/soil-moisture-alert.js");

test("moisture thresholds classify normal, elevated, and high states", () => {
  assert.equal(classifyMoisture(0), "normal");
  assert.equal(classifyMoisture(59.999), "normal");
  assert.equal(classifyMoisture(60), "elevated");
  assert.equal(classifyMoisture(79.999), "elevated");
  assert.equal(classifyMoisture(80), "high");
  assert.equal(classifyMoisture(100), "high");
});

test("invalid and missing moisture values have no alert classification", () => {
  for (const value of [null, undefined, -0.1, 100.1, Number.NaN, "80"]) {
    assert.equal(classifyMoisture(value), null);
  }
});

test("stale and offline readings cannot produce a current high alert", () => {
  assert.equal(evaluateCurrentMoisture("stale", 86), null);
  assert.equal(evaluateCurrentMoisture("offline", 86), null);
  assert.equal(evaluateCurrentMoisture("no_data", 0), null);
  assert.equal(evaluateCurrentMoisture("online", 86), "high");
});
