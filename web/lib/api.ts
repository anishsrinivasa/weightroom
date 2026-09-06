import type { ZodType } from "zod";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The signed-in user's session token, or null when nobody is signed in.
 *
 * Read from Clerk's live instance rather than a cookie. Which cookies Clerk
 * sets, and under what names, is an implementation detail that has already
 * changed once -- and when it changes, a cookie-reading BFF does not fail
 * loudly, it just starts treating every signed-in user as anonymous. Asking
 * for the token is the supported route and it refreshes an expired one.
 */
export async function sessionToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const clerk = (window as unknown as {
    Clerk?: { session?: { getToken: () => Promise<string | null> } };
  }).Clerk;
  if (!clerk?.session) return null;
  try {
    return await clerk.session.getToken();
  } catch {
    // A token we could not mint is anonymity, not an error: the request still
    // goes, and the server decides whether that is allowed.
    return null;
  }
}

export async function keystoneRequest<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof Blob) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!headers.has("Authorization")) {
    const token = await sessionToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`/api/keystone${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  const body: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String(body.detail)
        : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    if (process.env.NODE_ENV !== "production") console.error(parsed.error);
    throw new ApiError("The server returned an incompatible response.", 502);
  }
  return parsed.data;
}

export function clientUploadUrl(url: string): string {
  if (url.startsWith("/")) return `/api/keystone${url}`;
  return url;
}
