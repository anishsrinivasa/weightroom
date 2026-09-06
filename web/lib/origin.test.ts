import { describe, expect, it } from "vitest";

import { sameOrigin } from "@/app/api/keystone/[...path]/route";

/** Minimal stand-in: sameOrigin only reads headers and nextUrl.host. */
function request(headers: Record<string, string>, nextUrlHost = "localhost:3000") {
  return {
    headers: new Headers(headers),
    nextUrl: { host: nextUrlHost },
  } as unknown as Parameters<typeof sameOrigin>[0];
}

describe("same-origin guard", () => {
  it("accepts an origin matching the host the browser connected to", () => {
    expect(sameOrigin(request({
      origin: "http://127.0.0.1:3000", host: "127.0.0.1:3000",
    }))).toBe(true);
  });

  it("accepts localhost the same way", () => {
    expect(sameOrigin(request({
      origin: "http://localhost:3000", host: "localhost:3000",
    }))).toBe(true);
  });

  // The regression: comparing against nextUrl.origin reported localhost even
  // when the browser used 127.0.0.1, refusing every mutation on that host.
  it("does not care which loopback name was used, only that they agree", () => {
    expect(sameOrigin(request(
      { origin: "http://127.0.0.1:3000", host: "127.0.0.1:3000" },
      "localhost:3000",
    ))).toBe(true);
  });

  it("refuses a genuinely different origin", () => {
    expect(sameOrigin(request({
      origin: "https://evil.example", host: "localhost:3000",
    }))).toBe(false);
  });

  it("refuses a mismatched port", () => {
    expect(sameOrigin(request({
      origin: "http://localhost:4000", host: "localhost:3000",
    }))).toBe(false);
  });

  it("refuses an unparseable origin", () => {
    expect(sameOrigin(request({ origin: "not-a-url", host: "localhost:3000" }))).toBe(false);
  });

  it("allows a request with no origin at all", () => {
    // Browsers always send Origin on mutations, so its absence means a
    // non-browser client, which carries no ambient credentials to abuse.
    expect(sameOrigin(request({ host: "localhost:3000" }))).toBe(true);
  });
});
