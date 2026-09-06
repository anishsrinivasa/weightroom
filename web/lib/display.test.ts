import { describe, expect, it } from "vitest";

import { evaluationProgress, formatDate, formatDateTime, rejectionDetail } from "./display";

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

describe("API timestamp formatting", () => {
  it("treats timezone-less database timestamps as UTC", () => {
    expect(formatDateTime("2026-09-06T07:37:17.934179")).toBe(
      formatDateTime("2026-09-06T07:37:17.934179Z"),
    );
    expect(formatDate("2026-09-06T07:37:17.934179")).toBe(
      formatDate("2026-09-06T07:37:17.934179Z"),
    );
  });
});
