import type { FileManifestEntry } from "@/lib/contracts";

// Hashing needs the whole file in memory. A real small checkpoint is a few
// hundred megabytes, which a browser handles; anything larger belongs in a
// CLI that can stream.
export const BROWSER_FILE_LIMIT = 512 * 1024 * 1024;

export async function sha256Hex(data: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function manifestDigest(files: FileManifestEntry[]): Promise<string> {
  const manifest = [...files]
    // Code-point order, matching Python's sorted(). localeCompare collates
    // case-insensitively, so "README.md" and "config.json" sort the other way
    // round from the server -- producing a different digest and a
    // certification that fails after the seller has already paid.
    .sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0))
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

const MAX_SAFETENSORS_HEADER_BYTES = 100 * 1024 * 1024;

export function safetensorsTensorSizes(data: ArrayBuffer): Map<string, number> {
  if (data.byteLength < 8) throw new Error("SafeTensors file is missing its header");

  const headerSize = Number(new DataView(data, 0, 8).getBigUint64(0, true));
  if (
    !Number.isSafeInteger(headerSize)
    || headerSize <= 0
    || headerSize > MAX_SAFETENSORS_HEADER_BYTES
    || headerSize > data.byteLength - 8
  ) {
    throw new Error("SafeTensors header size is invalid");
  }

  const decoded: unknown = JSON.parse(
    new TextDecoder().decode(new Uint8Array(data, 8, headerSize)),
  );
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) {
    throw new Error("SafeTensors header is invalid");
  }

  const tensors = new Map<string, number>();
  for (const [name, value] of Object.entries(decoded)) {
    if (name === "__metadata__") continue;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`SafeTensors tensor ${name} is invalid`);
    }
    const shape = (value as { shape?: unknown }).shape;
    if (
      !Array.isArray(shape)
      || !shape.every((dimension) => Number.isInteger(dimension) && Number(dimension) >= 0)
    ) {
      throw new Error(`SafeTensors tensor ${name} has an invalid shape`);
    }
    const count = shape.reduce<number>((product, dimension) => product * Number(dimension), 1);
    if (!Number.isSafeInteger(count)) {
      throw new Error(`SafeTensors tensor ${name} is too large to count safely`);
    }
    tensors.set(name, count);
  }
  return tensors;
}

export function formatParameterCount(count: number): string {
  if (count >= 1_000_000_000_000) return `${(count / 1_000_000_000_000).toFixed(2)}T`;
  if (count >= 1_000_000_000) return `${(count / 1_000_000_000).toFixed(2)}B`;
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(2)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(2)}K`;
  return count.toLocaleString("en-US");
}
