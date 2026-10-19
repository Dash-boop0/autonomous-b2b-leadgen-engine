import { describe, expect, it } from "vitest";
import { VERIFICATION_LEVELS, groupBy, humanize, pct, ratioText, shouldKeepAdvancing, statusTone, tierTone, validateWeights, weightShare } from "@/lib/format";

describe("rates", () => {
  it("shows n/a for zero denominators instead of 0%", () => {
    expect(pct({ numerator: 0, denominator: 0, value: null, formula: "x" })).toBe("n/a");
    expect(pct(null)).toBe("n/a");
  });
  it("formats percentages and ratios", () => {
    expect(pct({ numerator: 1, denominator: 3, value: 0.3333, formula: "x" }, 1)).toBe("33.3%");
    expect(ratioText({ numerator: 1, denominator: 3, value: 0.3333, formula: "x" })).toBe("1 / 3");
  });
});

describe("verification labels", () => {
  it("keeps each level distinct and honest about mocks/simulation", () => {
    const labels = Object.values(VERIFICATION_LEVELS).map((v) => v.label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(VERIFICATION_LEVELS.provider_verified.meaning).toMatch(/MOCK/);
    expect(VERIFICATION_LEVELS.deliverable_confirmed.meaning).toMatch(/SIMULATED/);
    expect(VERIFICATION_LEVELS.domain_has_mx.meaning).toMatch(/nothing about this mailbox/);
  });
});

describe("weights", () => {
  it("computes shares and validates", () => {
    expect(weightShare({ a: 1, b: 3 }, "b")).toBe(75);
    expect(weightShare({ a: 0 }, "a")).toBe(0);
    expect(validateWeights({ a: -1 })).not.toBeNull();
    expect(validateWeights({ a: 0, b: 0 })).not.toBeNull();
    expect(validateWeights({ a: 1 })).toBeNull();
  });
});

describe("tones and helpers", () => {
  it("maps tiers and statuses", () => {
    expect(tierTone("A")).toBe("good");
    expect(tierTone("disqualified")).toBe("bad");
    expect(statusTone("waiting_approval")).toBe("warn");
    expect(statusTone("blocked")).toBe("bad");
    expect(humanize("question_pricing")).toBe("Question pricing");
  });
  it("groups preserving order", () => {
    expect(groupBy([{ k: "a" }, { k: "b" }, { k: "a" }], (x) => x.k).map(([k, v]) => [k, v.length])).toEqual([
      ["a", 2],
      ["b", 1],
    ]);
  });
  it("only keeps advancing running runs", () => {
    expect(shouldKeepAdvancing("budget_exhausted", "running")).toBe(true);
    expect(shouldKeepAdvancing("waiting_approval", "waiting_approval")).toBe(false);
    expect(shouldKeepAdvancing("halted:emergency_stop", "running")).toBe(false);
  });
});

describe("formatValue", () => {
  it("renders criterion values readably", async () => {
    const { formatValue } = await import("@/lib/format");
    expect(formatValue(["hiring_operations", "erp_migration"])).toBe("Hiring operations, Erp migration");
    expect(formatValue([])).toBe("none");
    expect(formatValue("Logistics")).toBe("Logistics");
    expect(formatValue(null)).toBe("-");
  });
});
