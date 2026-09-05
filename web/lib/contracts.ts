import { z } from "zod";

export const listingStateSchema = z.enum([
  "draft",
  "pending_certification",
  "certifying",
  "certified",
  "listed",
  "rejected",
  "delisted",
  "withdrawn",
]);

export type ListingState = z.infer<typeof listingStateSchema>;

export const listingSummarySchema = z.object({
  listing_id: z.string(),
  title: z.string().nullable().optional(),
  artifact_digest: z.string(),
  state: listingStateSchema,
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  selected_benchmarks: z.array(z.string()),
  attempts: z.number().int().nonnegative(),
  grade: z.string().nullable(),
  safety_status: z.string(),
  verified: z.boolean(),
  can_publish: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const sellerListingsSchema = z.object({
  listings: z.array(listingSummarySchema),
});

export type ListingSummary = z.infer<typeof listingSummarySchema>;

const suiteResultSchema = z.object({
  suite_id: z.string(),
  display_name: z.string().nullable().optional(),
  status: z.string(),
  gate: z.boolean().default(false),
  declined: z.boolean().default(false),
  score: z.number().nullable().optional(),
  score_band: z.string().nullable().optional(),
});

const reportSchema = z.object({
  subject: z.object({
    total_bytes: z.number(),
    files: z.array(z.unknown()),
    lineage: z.array(z.object({ ref: z.string() }).passthrough()).default([]),
    license: z.record(z.string(), z.unknown()).nullable().optional(),
  }),
  environment: z.object({ sandboxed: z.boolean() }).passthrough(),
  suite_results: z.array(suiteResultSchema),
  rating: z.object({
    grade: z.string(),
    methodology_version: z.string(),
  }).passthrough(),
  signature: z.object({ key_id: z.string() }).nullable().optional(),
}).passthrough();

const safetyGateSchema = z.object({
  gate_id: z.string(),
  display_name: z.string(),
  status: z.string(),
  evidence: z.string().nullable().optional(),
  blocking: z.boolean(),
});

export const listingDetailSchema = z.object({
  listing_id: z.string(),
  title: z.string().nullable().optional(),
  state: listingStateSchema,
  artifact_digest: z.string(),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  attempts: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
  flagged_for_review: z.boolean().optional(),
  audience: z.string().optional(),
  report: reportSchema.optional(),
  safety_gates: z.object({
    overall: z.string(),
    gates: z.array(safetyGateSchema),
  }).optional(),
});

export type ListingDetail = z.infer<typeof listingDetailSchema>;
export type SafetyGate = z.infer<typeof safetyGateSchema>;

export const benchmarkSchema = z.object({
  suite_id: z.string(),
  display_name: z.string(),
  description: z.string(),
  mandatory: z.boolean(),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
});

export const benchmarksSchema = z.object({
  benchmarks: z.array(benchmarkSchema),
  mandatory_total: z.string(),
});

export type Benchmark = z.infer<typeof benchmarkSchema>;

export const artifactDeclarationSchema = z.object({
  digest: z.string(),
  already_stored: z.number().int().nonnegative(),
  upload_urls: z.record(z.string(), z.string()),
  complete: z.boolean(),
});

export const artifactFinalizedSchema = z.object({
  digest: z.string(),
  files: z.number().int().nonnegative(),
});

export const listingCreatedSchema = z.object({
  listing_id: z.string(),
  state: listingStateSchema,
});

export const quoteSchema = z.object({
  charge_id: z.string(),
  amount: z.string(),
  running: z.array(z.string()),
  declined: z.array(z.string()),
  chain: z.string(),
  address: z.string(),
});

export const chargeSchema = z.object({
  charge_id: z.string(),
  amount: z.string(),
  received: z.string().nullable(),
  chain: z.string(),
  address: z.string(),
  tx_hash: z.string().nullable().optional(),
  confirmations: z.number().int().nonnegative(),
  required_confirmations: z.number().int().positive(),
  settled: z.boolean(),
});

export type Charge = z.infer<typeof chargeSchema>;

export const confirmedSchema = z.object({
  listing_id: z.string(),
  state: listingStateSchema,
});

export const demoPaymentSchema = z.object({
  charge_id: z.string(),
  tx_hash: z.string(),
});

export type FileManifestEntry = {
  path: string;
  size_bytes: number;
  sha256: string;
};

export type SelectedFile = FileManifestEntry & { blob: File };
