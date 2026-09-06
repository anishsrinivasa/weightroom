/**
 * Files from a drop, including the contents of dropped folders.
 *
 * `DataTransfer.files` is empty when a directory is dropped -- the browser
 * offers directories only through the entries API. So the drop zone said "Drop
 * your model folder here", accepted the drop, and silently did nothing, which
 * is the natural gesture on macOS where the folder is dragged out of Finder.
 *
 * Two details this gets right that a naive walk does not:
 *
 * `readEntries` returns at most 100 entries per call and signals the end with
 * an empty batch. Calling it once truncates any folder with more files than
 * that -- silently, and a model directory with sharded weights is exactly the
 * case that exceeds it.
 *
 * Dropped files have no `webkitRelativePath`, so the path has to come from the
 * walk. Without it every file collapses to its bare name and two shards in
 * different subdirectories collide.
 */

type FileSystemEntryLike = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (cb: (file: File) => void, err: (error: unknown) => void) => void;
  createReader?: () => {
    readEntries: (
      cb: (entries: FileSystemEntryLike[]) => void,
      err: (error: unknown) => void,
    ) => void;
  };
};

/** A File carrying the path it had inside the dropped folder. */
export type DroppedFile = File & { relativePath: string };

function withPath(file: File, relativePath: string): DroppedFile {
  // Defined rather than assigned: `File` properties are read-only in some
  // engines, and a silent failure here loses the path for every file.
  return Object.defineProperty(file, "relativePath", {
    value: relativePath,
    enumerable: true,
    configurable: true,
  }) as DroppedFile;
}

function readAll(entry: FileSystemEntryLike): Promise<FileSystemEntryLike[]> {
  const reader = entry.createReader?.();
  if (!reader) return Promise.resolve([]);
  const collected: FileSystemEntryLike[] = [];
  return new Promise((resolve, reject) => {
    const next = () => {
      reader.readEntries((batch) => {
        // An empty batch is the end of the directory, not an empty directory.
        if (!batch.length) return resolve(collected);
        collected.push(...batch);
        next();
      }, reject);
    };
    next();
  });
}

async function walk(
  entry: FileSystemEntryLike,
  prefix: string,
  out: DroppedFile[],
): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile && entry.file) {
    const file = await new Promise<File | null>((resolve) => {
      entry.file!(resolve, () => resolve(null));
    });
    // A file we cannot read is skipped rather than failing the whole drop:
    // one unreadable item should not discard a folder the user just chose.
    if (file) out.push(withPath(file, path));
    return;
  }
  if (entry.isDirectory) {
    const children = await readAll(entry);
    for (const child of children) await walk(child, path, out);
  }
}

export async function filesFromDrop(transfer: DataTransfer): Promise<DroppedFile[]> {
  const items = Array.from(transfer.items ?? []);
  const entries = items
    .map((item) =>
      (item as unknown as {
        webkitGetAsEntry?: () => FileSystemEntryLike | null;
      }).webkitGetAsEntry?.() ?? null,
    )
    .filter((entry): entry is FileSystemEntryLike => entry !== null);

  if (!entries.length) {
    // No entries API, or plain files dropped: fall back to what is offered,
    // keeping each file's own name as its path.
    return Array.from(transfer.files).map((file) => withPath(file, file.name));
  }

  const out: DroppedFile[] = [];
  // Sequential rather than parallel: `webkitGetAsEntry` handles are only valid
  // during the drop event's turn in some browsers, and racing the reads is how
  // a large folder comes back half-empty.
  for (const entry of entries) await walk(entry, "", out);
  return out;
}
