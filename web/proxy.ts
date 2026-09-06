import { NextRequest, NextResponse } from "next/server";

// Clerk's frontend API, derived from the publishable key so it tracks the
// instance rather than being pinned to one by hand. `strict-dynamic` already
// covers the script Clerk injects -- a nonce-trusted script may load others --
// but it says nothing about where the page may connect or fetch images from,
// and those need naming or sign-in fails with a blocked request.
function clerkOrigins(): string[] {
  const key = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? "";
  const encoded = key.split("_").pop() ?? "";
  try {
    const host = Buffer.from(encoded, "base64").toString("utf8").replace(/\$$/, "");
    return host.includes(".") ? [`https://${host}`] : [];
  } catch {
    return [];
  }
}

function contentSecurityPolicy(nonce: string): string {
  const development = process.env.NODE_ENV !== "production";
  const clerk = clerkOrigins().join(" ");
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: https://img.clerk.com${clerk ? " " + clerk : ""}`,
    "font-src 'self'",
    `connect-src 'self' https:${development ? " http: ws: wss:" : ""}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
  ].join("; ");
}

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const policy = contentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
