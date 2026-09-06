import { describe, expect, it } from "vitest";

import { evaluationProgressGateSchema } from "./contracts";

/**
 * The API returned 200 and the client rejected the body, which surfaces as
 * "The server returned an incompatible response." and looks like an outage.
 *
 * It happened because the server grew a `kind` the enum did not list and a row
 * shape without `completed`/`total`. Nothing failed on either side alone: the
 * Python tests asserted the dict, the web tests asserted the schema, and the
 * two drifted apart in between.
 *
 * These are the exact payloads `initial_progress_gates` and the Modal progress
 * pump emit. If a row shape changes on the server without the contract moving
 * with it, this fails instead of the seller's page.
 */
const check = (row: unknown) => evaluationProgressGateSchema.safeParse(row);

describe("evaluation progress contract", () => {
  it("accepts the general safety screens", () => {
    const parsed = check(
      { gate_id: "harmbench", display_name: "HarmBench harmful-output resistance",
        kind: "safety", status: "pending", completed: 0, total: 148, score: null },
    );
    expect(parsed.success).toBe(true);
  });

  it("accepts a conditioned gate that reports a percentage and no counts", () => {
    const parsed = check(
      { gate_id: "bio_elicitation", display_name: "Bio elicitation resistance",
        kind: "conditioned", status: "conditional", percent: 0,
        completed: 0, total: 0, score: null },
    );
    expect(parsed.success).toBe(true);
  });

  it("accepts a conditioned gate with counts omitted entirely", () => {
    const parsed = check(
      { gate_id: "legal_elicitation", display_name: "Legal elicitation resistance",
        kind: "conditioned", status: "not_required", percent: 100 },
    );
    expect(parsed.success).toBe(true);
  });

  it("accepts the public benchmark rows", () => {
    const parsed = check(
      { gate_id: "mmlu_pro", display_name: "MMLU-Pro", kind: "benchmark",
        status: "running", completed: 12, total: 100, score: null },
    );
    expect(parsed.success).toBe(true);
  });

  it("still rejects a kind nobody taught the client about", () => {
    const parsed = check(
      { gate_id: "x", display_name: "X", kind: "screen", status: "pending",
        completed: 0, total: 1 },
    );
    expect(parsed.success).toBe(false);
  });

  it("rejects a percentage outside 0-100", () => {
    const parsed = check(
      { gate_id: "bio_elicitation", display_name: "Bio", kind: "conditioned",
        status: "running", percent: 140 },
    );
    expect(parsed.success).toBe(false);
  });
});
