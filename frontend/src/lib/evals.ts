import type { EvalMetrics } from "@/lib/types";

/**
 * `part` of `whole` as a percentage with one decimal, truncated like the backend report
 * (79 of 80 shows as 98.7%, never 98.8%). Integer arithmetic, so no float rounding creeps in.
 */
export function truncatedPercent(part: number, whole: number): string {
  if (whole <= 0) return "n/a";
  const tenths = Math.floor((part * 1000) / whole);
  return `${Math.floor(tenths / 10)}.${tenths % 10}%`;
}

const CATEGORY_NAMES: Record<string, string> = {
  refund_under_50: "Refund up to $50",
  refund_over_50: "Refund $50 to $500",
  over_500: "Over $500",
  supervisor_rejects: "Supervisor rejects",
  outside_window: "Outside the window",
  wrong_owner: "Someone else's order",
  already_remedied: "Already refunded or replaced",
  not_delivered: "Not delivered yet",
  overdue: "Late delivery",
  missing_order: "Where is my order",
  replacement: "Replacement",
  cancellation: "Cancellation",
  billing: "Billing question",
  prompt_injection: "Prompt injection",
  social_engineering: "Pressure and persuasion",
  vague: "Needs the order",
  unknown_intent: "Unsupported request",
  tool_failure: "Shop failures",
  fraud_flag: "Flagged account",
  partial_item: "One item of several",
};

export function categoryName(category: string): string {
  return CATEGORY_NAMES[category] ?? category.replaceAll("_", " ");
}

export interface CategoryRow {
  category: string;
  name: string;
  passed: number;
  cases: number;
}

/** Categories by pass rate, highest first; ties keep a stable alphabetical order. */
export function categoryRows(metrics: EvalMetrics): CategoryRow[] {
  return Object.entries(metrics.by_category)
    .map(([category, score]) => ({ category, name: categoryName(category), ...score }))
    .sort((a, b) => b.passed * a.cases - a.passed * b.cases || a.name.localeCompare(b.name));
}

/** "9.8 s" from milliseconds, truncated to one decimal. */
export function seconds(ms: number): string {
  return `${(Math.floor(ms / 100) / 10).toFixed(1)} s`;
}

/** Dollars as recorded, without rounding: "$0.012163". */
export function exactUsd(amount: string): string {
  return Number(amount) === 0 ? "$0" : `$${amount}`;
}

/** A pre-run estimate for `cases` cases, from the full-dataset estimate. */
export function estimateFor(cases: number, fullEstimate: string, datasetCases: number): string {
  if (datasetCases <= 0) return "$0";
  return `about $${((Number(fullEstimate) * cases) / datasetCases).toFixed(2)}`;
}
