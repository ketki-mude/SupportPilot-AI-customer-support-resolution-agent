import { describe, expect, it } from "vitest";

import {
  categoryName,
  categoryRows,
  estimateFor,
  exactUsd,
  seconds,
  temperatureLabel,
  truncatedPercent,
} from "@/lib/evals";
import { evalSummary } from "@/test/fixtures";

describe("evaluation formatting", () => {
  it.each([
    [79, 80, "98.7%"],
    [2, 3, "66.6%"],
    [1, 8, "12.5%"],
    [80, 80, "100.0%"],
    [0, 5, "0.0%"],
    [0, 0, "n/a"],
  ])("truncates %i of %i to %s, never rounding up", (part, whole, shown) => {
    expect(truncatedPercent(part, whole)).toBe(shown);
  });

  it("shows money exactly as recorded", () => {
    expect(exactUsd("0.104312")).toBe("$0.104312");
    expect(exactUsd("0.000000")).toBe("$0");
  });

  it("truncates latency to a tenth of a second", () => {
    expect(seconds(8199)).toBe("8.1 s");
    expect(seconds(0)).toBe("0.0 s");
  });

  it("says how a run sampled", () => {
    expect(temperatureLabel({ llm_mode: "live", temperature: 0 })).toBe("temperature 0");
    expect(temperatureLabel({ llm_mode: "live", temperature: null })).toBe("model's fixed default temperature");
    expect(temperatureLabel({ llm_mode: "mock", temperature: null })).toBe("no sampling");
  });

  it("scales the full-run estimate to the chosen size", () => {
    expect(estimateFor(10, "0.2208", 80)).toBe("about $0.03");
    expect(estimateFor(80, "0.2208", 80)).toBe("about $0.22");
  });

  it("orders categories by pass rate and names them for people", () => {
    const rows = categoryRows(evalSummary().metrics);
    expect(rows.map((row) => row.category)).toEqual(["billing", "refund_under_50", "partial_item"]);
    expect(rows[2]).toMatchObject({ name: "One item of several", passed: 1, cases: 2 });
    expect(categoryName("brand_new_case")).toBe("brand new case");
  });
});
