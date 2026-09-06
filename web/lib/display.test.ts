import { describe, expect, it } from "vitest";

import { evaluationProgress, rejectionDetail } from "./display";

describe("evaluationProgress", () => {
  it("distinguishes waiting in the queue from active evaluation", () => {
    expect(evaluationProgress("pending_certification")).toEqual({
      heading: "Queued for evaluation",
      detail: "Your payment is confirmed. The evaluation worker has not claimed this model yet.",
    });
    expect(evaluationProgress("certifying")?.heading).toBe("Evaluation in progress");
  });

  it("does not report terminal states as active", () => {
    expect(evaluationProgress("certified")).toBeNull();
    expect(evaluationProgress("rejected")).toBeNull();
  });
});

describe("rejectionDetail", () => {
  it("does not call an unavailable gate a failed gate", () => {
    expect(rejectionDetail("pending")).toContain("unavailable or incomplete");
    expect(rejectionDetail("pending")).not.toContain("gate failed");
  });

  it("identifies an actual mandatory failure", () => {
    expect(rejectionDetail("fail")).toContain("mandatory gate failed");
  });
});
