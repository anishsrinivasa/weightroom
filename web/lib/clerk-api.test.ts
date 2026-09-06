import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * `<SignedIn>`, `<SignedOut>` and `<Protect>` were removed in Clerk's Core 3.
 * They are still exported, so importing them typechecks and builds -- but the
 * implementation is a stub that throws the moment it renders.
 *
 * That combination is the dangerous one: CI was green, the deploy succeeded,
 * and every rendered page returned 500 while the API routes kept answering.
 * Nothing catches it until a real request arrives.
 *
 * A grep is a blunt instrument, but it fails at the same moment a typecheck
 * would have if these had been deleted rather than tombstoned.
 */
const REMOVED = ["SignedIn", "SignedOut", "Protect"];
const ROOTS = ["app", "components", "lib"];
const SOURCE = /\.tsx?$/;

function sourceFiles(dir: string): string[] {
  const here = join(process.cwd(), dir);
  const found: string[] = [];
  const walk = (path: string) => {
    for (const entry of readdirSync(path)) {
      if (entry === "node_modules" || entry.startsWith(".")) continue;
      const full = join(path, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (SOURCE.test(entry)) found.push(full);
    }
  };
  try {
    walk(here);
  } catch {
    // A root that does not exist contributes nothing rather than failing.
  }
  return found;
}

describe("Clerk Core 3 compatibility", () => {
  const files = ROOTS.flatMap(sourceFiles);

  it("finds source files to check", () => {
    // Guards the guard: a walk that silently returned nothing would make every
    // assertion below vacuously pass.
    expect(files.length).toBeGreaterThan(5);
  });

  it.each(REMOVED)("does not import the removed <%s> component", (name) => {
    const offenders = files.filter((file) => {
      const text = readFileSync(file, "utf8");
      const imports = text.match(/import\s*\{[^}]*\}\s*from\s*["']@clerk\/nextjs["']/g) ?? [];
      return imports.some((line) => new RegExp(`\\b${name}\\b`).test(line));
    });
    expect(offenders).toEqual([]);
  });

  it("uses the supported hook where auth state is branched on", () => {
    const header = readFileSync(join(process.cwd(), "components/Header.tsx"), "utf8");
    expect(header).toContain("useAuth");
  });
});
