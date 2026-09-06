import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatParameterCount,
  manifestDigest,
  safetensorsTensorSizes,
  sha256Hex,
} from "@/lib/artifact";

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

  // The server recomputes this digest and rejects a mismatch, so the two
  // implementations are one contract. This vector is asserted identically by
  // test_manifest_digest_algorithm_is_pinned in tests/test_offline.py; if one
  // side changes, both must.
  it("matches the digest the server computes", async () => {
    await expect(
      manifestDigest([
        { path: "model.safetensors", size_bytes: 4096, sha256: "b".repeat(64) },
        { path: "config.json", size_bytes: 120, sha256: "a".repeat(64) },
      ]),
    ).resolves.toBe(
      "9f51a3e20eaa31068289daf1a6e0845c0f738576335573c7fa8550b9d4d73962",
    );
  });

  // Uppercase sorts before lowercase by code point but after it under most
  // locale collations. A repo with a README is the common case.
  it("orders capitalised paths the way the server does", async () => {
    await expect(
      manifestDigest([
        { path: "config.json", size_bytes: 1, sha256: "b".repeat(64) },
        { path: "README.md", size_bytes: 1, sha256: "a".repeat(64) },
      ]),
    ).resolves.toBe(
      "27101854f6e81e27e2a39a1397c0ca307ab5659c30220110749d14d51674ea75",
    );
  });
});

describe("formatBytes", () => {
  it("uses readable binary units", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 ** 3)).toBe("1.00 GB");
  });
});

describe("SafeTensors parameter detection", () => {
  function checkpoint(header: object): ArrayBuffer {
    const encoded = new TextEncoder().encode(JSON.stringify(header));
    const bytes = new Uint8Array(8 + encoded.length);
    new DataView(bytes.buffer).setBigUint64(0, BigInt(encoded.length), true);
    bytes.set(encoded, 8);
    return bytes.buffer;
  }

  it("counts tensor shapes without loading tensor data", () => {
    const tensors = safetensorsTensorSizes(checkpoint({
      __metadata__: { format: "pt" },
      "model.embed_tokens.weight": { dtype: "F16", shape: [32, 8], data_offsets: [0, 512] },
      "model.layers.0.weight": { dtype: "F16", shape: [8, 8], data_offsets: [512, 640] },
    }));

    expect([...tensors.values()].reduce((total, size) => total + size, 0)).toBe(320);
  });

  it("rejects malformed headers instead of guessing", () => {
    expect(() => safetensorsTensorSizes(new ArrayBuffer(7))).toThrow(/header/i);
    expect(() => safetensorsTensorSizes(checkpoint({ weight: { shape: [4, -1] } }))).toThrow(/shape/i);
  });

  it("formats model-scale counts", () => {
    expect(formatParameterCount(134_515_008)).toBe("134.52M");
    expect(formatParameterCount(7_242_000_000)).toBe("7.24B");
  });
});
