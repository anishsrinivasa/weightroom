import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BODY_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
]);

function upstreamBase(): URL {
  const value = process.env.KEYSTONE_API_URL;
  if (!value) throw new Error("KEYSTONE_API_URL is not configured");
  return new URL(value.endsWith("/") ? value : `${value}/`);
}

function safePath(parts: string[]): string {
  if (!parts.length || parts[0] !== "v1") throw new Error("Unsupported API path");
  if (parts.some((part) => !part || part === "." || part === ".." || /[\\/]/.test(part))) {
    throw new Error("Invalid API path");
  }
  return parts.map(encodeURIComponent).join("/");
}

export function accessToken(request: NextRequest): string | null {
  const incoming = request.headers.get("authorization");
  if (incoming?.toLowerCase().startsWith("bearer ")) return incoming.slice(7).trim();

  const cookieName = process.env.KEYSTONE_SESSION_COOKIE || "keystone_access_token";
  const cookieToken = request.cookies.get(cookieName)?.value;
  if (cookieToken) return cookieToken;

  // An explicitly configured, server-only token supports single-tenant demo
  // deployments until the OIDC callback is wired. Never expose this through
  // a NEXT_PUBLIC_* variable: every browser request should still hit this BFF.
  const serverToken = process.env.KEYSTONE_SERVER_TOKEN?.trim();
  if (serverToken) return serverToken;

  if (process.env.NODE_ENV !== "production") return process.env.KEYSTONE_DEV_TOKEN || null;
  return null;
}

export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  // Browsers attach Origin to every mutating request, so its absence means a
  // non-browser client -- which carries no ambient credentials to abuse.
  if (!origin) return true;

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }

  // Compare against the Host header, which is what the browser actually
  // connected to. `nextUrl.origin` is Next's own idea of the URL and reports
  // localhost even when the browser used 127.0.0.1, refusing every mutation
  // for a request that was same-origin all along.
  const host = request.headers.get("host") ?? request.nextUrl.host;
  if (originHost === host) return true;

  // Behind a proxy that rewrites Host, name the public origins explicitly.
  return (process.env.KEYSTONE_ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean)
    .includes(origin);
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  try {
    if (BODY_METHODS.has(request.method) && !sameOrigin(request)) {
      return NextResponse.json({ detail: "Cross-origin mutation refused" }, { status: 403 });
    }

    const { path } = await context.params;
    const url = new URL(safePath(path), upstreamBase());
    url.search = request.nextUrl.search;
    const streamsDownload = request.method === "GET"
      && path.length === 4
      && path[0] === "v1"
      && path[1] === "listings"
      && path[3] === "download.zip";

    const headers = new Headers();
    const contentType = request.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    headers.set("accept", request.headers.get("accept") || "application/json");
    headers.set("x-forwarded-host", request.nextUrl.host);
    headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
    const requestId = request.headers.get("x-request-id") || crypto.randomUUID();
    headers.set("x-request-id", requestId);
    const token = accessToken(request);
    if (token) headers.set("authorization", `Bearer ${token}`);

    const body = BODY_METHODS.has(request.method) ? await request.arrayBuffer() : undefined;
    const upstream = await fetch(url, {
      method: request.method,
      headers,
      body: body?.byteLength ? body : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: streamsDownload ? undefined : AbortSignal.timeout(30_000),
    });

    const responseHeaders = new Headers();
    upstream.headers.forEach((value, key) => {
      if (!HOP_BY_HOP.has(key.toLowerCase()) && key.toLowerCase() !== "set-cookie") {
        responseHeaders.set(key, value);
      }
    });
    responseHeaders.set("cache-control", "private, no-store");
    responseHeaders.set("x-request-id", requestId);
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const message = process.env.NODE_ENV === "production"
      ? "The marketplace service is temporarily unavailable"
      : error instanceof Error ? error.message : "API gateway failure";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
