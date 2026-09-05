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

function accessToken(request: NextRequest): string | null {
  const incoming = request.headers.get("authorization");
  if (incoming?.toLowerCase().startsWith("bearer ")) return incoming.slice(7).trim();

  const cookieName = process.env.KEYSTONE_SESSION_COOKIE || "keystone_access_token";
  const cookieToken = request.cookies.get(cookieName)?.value;
  if (cookieToken) return cookieToken;

  if (process.env.NODE_ENV !== "production") return process.env.KEYSTONE_DEV_TOKEN || null;
  return null;
}

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  try {
    const origin = request.headers.get("origin");
    if (BODY_METHODS.has(request.method) && origin && origin !== request.nextUrl.origin) {
      return NextResponse.json({ detail: "Cross-origin mutation refused" }, { status: 403 });
    }

    const { path } = await context.params;
    const url = new URL(safePath(path), upstreamBase());
    url.search = request.nextUrl.search;

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
      signal: AbortSignal.timeout(30_000),
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
