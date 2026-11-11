const test = require("node:test");
const assert = require("node:assert/strict");

const { sortVillageRecords } = require("../frontend/js/ranking.js");

const villages = [
  { village_name_en: "Beta", scenario_susceptibility_0_100: 20 },
  { village_name_en: "Alpha", scenario_susceptibility_0_100: 80 },
  { village_name_en: "Gamma", scenario_susceptibility_0_100: null }
];

test("village ranking sorts scores in either direction and leaves missing scores last", () => {
  assert.deepEqual(sortVillageRecords(villages).map(v => v.village_name_en), ["Alpha", "Beta", "Gamma"]);
  assert.deepEqual(sortVillageRecords(villages, "score-asc").map(v => v.village_name_en), ["Beta", "Alpha", "Gamma"]);
});

test("village ranking sorts names case-insensitively without mutating input", () => {
  const sorted = sortVillageRecords(villages, "name-asc");
  assert.deepEqual(sorted.map(v => v.village_name_en), ["Alpha", "Beta", "Gamma"]);
  assert.equal(villages[0].village_name_en, "Beta");
});
