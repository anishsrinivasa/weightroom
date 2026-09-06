import { describe, expect, it } from "vitest";

import { normalizedRelativePath } from "./artifact";
import { filesFromDrop } from "./dropped-files";

/**
 * The drop zone said "Drop your model folder here", accepted the drop, and did
 * nothing -- `DataTransfer.files` is empty for a directory, and directories are
 * only reachable through the entries API.
 *
 * These fake that API rather than a browser, so the two details that actually
 * bite are testable: `readEntries` caps each batch at 100 and signals the end
 * with an empty one, and dropped files carry no `webkitRelativePath`.
 */

// Declared rather than inferred: `dirEntry` holds children of the same type,
// and inferring the alias from its own return type is circular.
type Entry = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (cb: (file: File) => void, err: (error: unknown) => void) => void;
  createReader?: () => {
    readEntries: (cb: (entries: Entry[]) => void, err: (error: unknown) => void) => void;
  };
};

function fileEntry(name: string, size = 8): Entry {
  return {
    isFile: true,
    isDirectory: false,
    name,
    file: (cb: (f: File) => void) =>
      cb(new File([new Uint8Array(size)], name)),
  };
}

function dirEntry(name: string, children: Entry[]): Entry {
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => {
      // Real readers hand back at most 100 per call and an empty array at the
      // end. A walk that reads once silently truncates a sharded checkpoint.
      let cursor = 0;
      return {
        readEntries: (cb: (batch: Entry[]) => void) => {
          const batch = children.slice(cursor, cursor + 100);
          cursor += batch.length;
          cb(batch);
        },
      };
    },
  };
}

function transferOf(entries: Entry[], plainFiles: File[] = []): DataTransfer {
  return {
    items: entries.map((entry) => ({ webkitGetAsEntry: () => entry })),
    files: plainFiles,
  } as unknown as DataTransfer;
}

describe("files from a drop", () => {
  it("reads a dropped folder, which dataTransfer.files does not expose", async () => {
    const files = await filesFromDrop(transferOf([
      dirEntry("model", [fileEntry("config.json"), fileEntry("model.safetensors")]),
    ]));
    expect(files.map((f) => f.name).sort()).toEqual([
      "config.json", "model.safetensors",
    ]);
  });

  it("does not truncate a folder larger than one readEntries batch", async () => {
    // 250 shards: a single read would return 100 and lose the rest, and the
    // upload would succeed against an incomplete manifest.
    const shards = Array.from({ length: 250 }, (_, i) => fileEntry(`shard-${i}.safetensors`));
    const files = await filesFromDrop(transferOf([dirEntry("model", shards)]));
    expect(files).toHaveLength(250);
  });

  it("keeps each file's path within the dropped folder", async () => {
    const files = await filesFromDrop(transferOf([
      dirEntry("model", [
        fileEntry("config.json"),
        dirEntry("nested", [fileEntry("config.json")]),
      ]),
    ]));
    expect(files.map((f) => f.relativePath).sort()).toEqual([
      "model/config.json", "model/nested/config.json",
    ]);
  });

  it("gives two same-named files in different folders distinct paths", async () => {
    /** Without the walked path both collapse to "config.json" and collide. */
    const files = await filesFromDrop(transferOf([
      dirEntry("model", [
        fileEntry("config.json"),
        dirEntry("nested", [fileEntry("config.json")]),
      ]),
    ]));
    const paths = files.map(normalizedRelativePath);
    expect(new Set(paths).size).toBe(2);
  });

  it("strips the top folder from the stored path", async () => {
    /** The manifest is relative to the model root, not to the user's disk. */
    const files = await filesFromDrop(transferOf([
      dirEntry("SmolLM2-135M", [fileEntry("config.json")]),
    ]));
    expect(normalizedRelativePath(files[0])).toBe("config.json");
  });

  it("handles loose files dropped without a folder", async () => {
    const files = await filesFromDrop(transferOf([fileEntry("config.json")]));
    expect(files.map((f) => f.relativePath)).toEqual(["config.json"]);
  });

  it("falls back to dataTransfer.files when there is no entries API", async () => {
    const plain = new File([new Uint8Array(4)], "config.json");
    const files = await filesFromDrop({
      items: [],
      files: [plain],
    } as unknown as DataTransfer);
    expect(files.map((f) => f.name)).toEqual(["config.json"]);
  });

  it("skips an unreadable file rather than discarding the drop", async () => {
    const broken = {
      isFile: true, isDirectory: false, name: "locked.bin",
      file: (_cb: (f: File) => void, err: (e: unknown) => void) => err(new Error("EPERM")),
    };
    const files = await filesFromDrop(transferOf([
      dirEntry("model", [fileEntry("config.json"), broken as Entry]),
    ]));
    expect(files.map((f) => f.name)).toEqual(["config.json"]);
  });

  it("returns nothing for an empty folder rather than hanging", async () => {
    expect(await filesFromDrop(transferOf([dirEntry("empty", [])]))).toEqual([]);
  });
});
