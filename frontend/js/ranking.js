(() => {
  "use strict";

  function sortVillageRecords(records, sortKey = "score-desc") {
    const scoreOf = record => {
      const raw = record.scenario_susceptibility_0_100 ?? record.ml_susceptibility_0_100;
      if (raw === null || raw === undefined || raw === "") return null;
      const value = Number(raw);
      return Number.isFinite(value) ? value : null;
    };

    return [...records].sort((left, right) => {
      if (sortKey === "name-asc") {
        return String(left.village_name_en || "").localeCompare(
          String(right.village_name_en || ""), undefined, { sensitivity: "base" }
        );
      }
      const leftScore = scoreOf(left);
      const rightScore = scoreOf(right);
      if (leftScore === null || rightScore === null) {
        if (leftScore === rightScore) return 0;
        return leftScore === null ? 1 : -1;
      }
      return sortKey === "score-asc" ? leftScore - rightScore : rightScore - leftScore;
    });
  }

  const api = { sortVillageRecords };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.FloodGuardRanking = api;
})();
