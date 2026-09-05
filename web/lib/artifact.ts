import type { FileManifestEntry } from "@/lib/contracts";

export const BROWSER_FILE_LIMIT = 64 * 1024 * 1024;

export async function sha256Hex(data: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function manifestDigest(files: FileManifestEntry[]): Promise<string> {
  const manifest = [...files]
    .sort((left, right) => left.path.localeCompare(right.path))
    .map((file) => `${file.path}:${file.sha256}\n`)
    .join("");
  return sha256Hex(new TextEncoder().encode(manifest));
}

export function normalizedRelativePath(file: File): string {
  const parts = (file.webkitRelativePath || file.name).split("/");
  return parts.length > 1 ? parts.slice(1).join("/") : parts[0];
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
