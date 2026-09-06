import { afterEach, describe, expect, it } from "vitest";

import { accessToken } from "@/app/api/keystone/[...path]/route";

const ORIGINAL_ENV = { ...process.env };

function setNodeEnv(value: string) {
  Object.defineProperty(process.env, "NODE_ENV", {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  });
}

function request({
  authorization,
  cookie,
}: { authorization?: string; cookie?: string } = {}) {
  return {
    headers: new Headers(authorization ? { authorization } : {}),
    cookies: {
      get: () => cookie ? { value: cookie } : undefined,
    },
  } as unknown as Parameters<typeof accessToken>[0];
}

afterEach(() => {
  process.env = { ...ORIGINAL_ENV };
});

describe("Keystone BFF authentication", () => {
  it("prefers a caller bearer token", () => {
    process.env.KEYSTONE_SERVER_TOKEN = "server-token";
    expect(accessToken(request({ authorization: "Bearer caller-token" }))).toBe("caller-token");
  });

  it("prefers the secure session cookie over the deployment token", () => {
    process.env.KEYSTONE_SERVER_TOKEN = "server-token";
    expect(accessToken(request({ cookie: "session-token" }))).toBe("session-token");
  });

  it("uses an explicitly configured server token in production", () => {
    setNodeEnv("production");
    process.env.KEYSTONE_SERVER_TOKEN = "  server-token  ";
    expect(accessToken(request())).toBe("server-token");
  });

  it("does not fall back to the development token in production", () => {
    setNodeEnv("production");
    process.env.KEYSTONE_DEV_TOKEN = "dev-token";
    delete process.env.KEYSTONE_SERVER_TOKEN;
    expect(accessToken(request())).toBeNull();
  });
});
