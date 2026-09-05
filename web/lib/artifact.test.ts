import { describe, expect, it } from "vitest";

import { formatBytes, manifestDigest, sha256Hex } from "@/lib/artifact";

describe("artifact identity", () => {
  it("computes the standard SHA-256 vector", async () => {
    await expect(sha256Hex(new TextEncoder().encode("abc"))).resolves.toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("is stable regardless of file selection order", async () => {
    const left = [
      { path: "weights.safetensors", size_bytes: 2, sha256: "b".repeat(64) },
      { path: "config.json", size_bytes: 1, sha256: "a".repeat(64) },
    ];
    await expect(manifestDigest(left)).resolves.toBe(
      await manifestDigest([...left].reverse()),
    );
  });
});

describe("formatBytes", () => {
  it("uses readable binary units", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 ** 3)).toBe("1.00 GB");
  });
});
