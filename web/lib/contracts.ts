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
  description: z.string().nullable().optional(),
  image_url: z.string().nullable().optional(),
  artifact_digest: z.string(),
  state: listingStateSchema,
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  domain_tags: z.array(z.string()).default([]),
  size_tag: z.string().nullable().default(null),
  selected_benchmarks: z.array(z.string()),
  attempts: z.number().int().nonnegative(),
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
  serving_profile: z.object({
    architecture: z.string().nullable().optional(),
    parameter_count: z.number().int().nonnegative().nullable().optional(),
    max_context: z.number().int().nonnegative().nullable().optional(),
  }).passthrough(),
  suite_results: z.array(suiteResultSchema),
  rating: z.object({
    certified: z.boolean().optional(),
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
  score: z.number().nullable().optional(),
  n_items: z.number().int().nonnegative().nullable().optional(),
});

export const voteTallySchema = z.object({
  up: z.number().int().nonnegative(),
  down: z.number().int().nonnegative(),
  score: z.number().int(),
  // This viewer's own vote: +1, -1, or 0. Without it the UI cannot tell an
  // unvoted listing from one this person already voted on.
  mine: z.number().int().min(-1).max(1),
});

export const licenseTermsSchema = z.object({
  kind: z.enum(["non_distributive", "full_access"]),
  display_name: z.string(),
  summary: z.string(),
  agreement: z.string(),
});

export type VoteTally = z.infer<typeof voteTallySchema>;
export type LicenseTerms = z.infer<typeof licenseTermsSchema>;

export const licenseCatalogueSchema = z.object({
  licenses: z.array(licenseTermsSchema.extend({ default: z.boolean() })),
  default: z.enum(["non_distributive", "full_access"]),
});

export const evaluationProgressGateSchema = z.object({
  gate_id: z.string(),
  display_name: z.string(),
  kind: z.enum(["safety", "benchmark", "conditioned"]).optional(),
  status: z.string(),
  // Conditioned gates report a percentage and never counts: their item budget
  // is derived from the tolerated-harm ceiling the model's capability earns,
  // so "0 of 128" against "0 of 192" would print the capability band onto the
  // seller's progress bar. Counts stay required in practice for the other
  // kinds; optional here so one row shape covers both.
  completed: z.number().int().nonnegative().optional(),
  total: z.number().int().nonnegative().optional(),
  percent: z.number().min(0).max(100).optional(),
  score: z.number().nullable().optional(),
});

const modelSourceSchema = z.object({
  kind: z.enum(["huggingface", "upload"]),
  ref: z.string(),
  revision: z.string().nullable(),
});

export const listingDetailSchema = z.object({
  listing_id: z.string(),
  license: licenseTermsSchema.optional(),
  votes: voteTallySchema.optional(),
  title: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  image_url: z.string().nullable().optional(),
  state: listingStateSchema,
  artifact_digest: z.string(),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  seller_id: z.string(),
  source: modelSourceSchema.nullable().optional(),
  benchmark_scores: z.record(z.string(), z.number()),
  selected_benchmarks: z.array(z.string()).default([]),
  is_owner: z.boolean(),
  entitled: z.boolean(),
  domain_tags: z.array(z.string()).default([]),
  size_tag: z.string().nullable().default(null),
  attempts: z.number().int().nonnegative(),
  created_at: z.string(),
  updated_at: z.string(),
  artifact: z.object({
    files: z.array(z.object({
      path: z.string(),
      size_bytes: z.number().int().positive(),
      sha256: z.string().regex(/^[0-9a-f]{64}$/),
    })),
    total_bytes: z.number().int().nonnegative(),
  }).optional(),
  flagged_for_review: z.boolean().optional(),
  audience: z.string().optional(),
  report: reportSchema.optional(),
  safety_gates: z.object({
    overall: z.string(),
    gates: z.array(safetyGateSchema),
  }).optional(),
  evaluation_progress: z.object({
    percent: z.number().int().min(0).max(100),
    stage: z.string(),
    gates: z.array(evaluationProgressGateSchema),
    updated_at: z.string(),
  }).optional(),
});

export type ListingDetail = z.infer<typeof listingDetailSchema>;
export type SafetyGate = z.infer<typeof safetyGateSchema>;

export const benchmarkSchema = z.object({
  suite_id: z.string(),
  display_name: z.string(),
  description: z.string(),
  gate: z.boolean(),
  diagnostic: z.boolean(),
  held_out: z.boolean(),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  price_is_estimate: z.boolean().default(false),
  score_direction: z.enum(["higher", "lower"]).default("higher"),
  harness_kind: z.enum(["agent", "multiple_choice", "expert_math"]).optional(),
  task_count: z.number().int().positive(),
  sample_size: z.number().int().positive(),
  sampling_strategy: z.literal("deterministic_random_without_replacement"),
  sampling_seed_version: z.string(),
  source_url: z.string().url().optional(),
});

export const safetyEvaluationSchema = z.object({
  suite_id: z.literal("safety_evaluation"),
  display_name: z.string(),
  description: z.string(),
  required: z.literal(true),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  price_is_estimate: z.boolean(),
  screen_ids: z.array(z.string()),
  automatic_pass: z.boolean().default(false),
});

export const benchmarksSchema = z.object({
  safety_evaluation: safetyEvaluationSchema,
  benchmarks: z.array(benchmarkSchema),
  pricing_basis: z.object({
    estimated: z.boolean(),
    model_weight_bytes: z.number().int().nonnegative().nullable(),
    sample_size_per_benchmark: z.number().int().positive(),
    sampling_strategy: z.literal("deterministic_random_without_replacement"),
  }).optional(),
});

export type Benchmark = z.infer<typeof benchmarkSchema>;
export type SafetyEvaluation = z.infer<typeof safetyEvaluationSchema>;

export const tagOptionSchema = z.object({
  id: z.string(),
  label: z.string(),
});

export const tagCatalogueSchema = z.object({
  domains: z.array(tagOptionSchema),
  model_sizes: z.array(tagOptionSchema),
});

export type TagOption = z.infer<typeof tagOptionSchema>;
export type TagCatalogue = z.infer<typeof tagCatalogueSchema>;

export const publicListingSchema = z.object({
  listing_id: z.string(),
  title: z.string().nullable().optional(),
  description: z.string().nullable().optional(),
  image_url: z.string().nullable().optional(),
  artifact_digest: z.string(),
  state: z.literal("listed"),
  price: z.string(),
  price_minor: z.number().int().nonnegative(),
  seller_id: z.string(),
  source: modelSourceSchema.nullable().optional(),
  benchmark_scores: z.record(z.string(), z.number()),
  // Optional so a client that predates the field still parses a response from
  // a newer server, and vice versa. A contract that hard-fails on a field
  // either side has not shipped yet is a deploy-order trap.
  license_kind: z.enum(["non_distributive", "full_access"]).optional(),
  votes: voteTallySchema.optional(),
  domain_tags: z.array(z.string()).default([]),
  size_tag: z.string().nullable().default(null),
  created_at: z.string(),
});

export const publicListingsSchema = z.object({
  listings: z.array(publicListingSchema),
});

export type PublicListing = z.infer<typeof publicListingSchema>;

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

export const imageStoredSchema = z.object({
  image_digest: z.string(),
  content_type: z.string(),
  bytes: z.number().int().positive(),
});

export const quoteSchema = z.object({
  charge_id: z.string(),
  amount: z.string(),
  safety_evaluation: safetyEvaluationSchema,
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

export const purchaseSchema = z.object({
  order_id: z.string(),
  amount: z.string(),
  charge_id: z.string(),
  chain: z.string(),
  address: z.string(),
  checkout_url: z.string().nullable().optional(),
});

export type Purchase = z.infer<typeof purchaseSchema>;

export const orderConfirmedSchema = z.object({
  order_id: z.string(),
  status: z.literal("paid"),
  entitled: z.literal(true),
});

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

export const sampleModelManifestSchema = z.object({
  name: z.string(),
  source: z.string(),
  digest: z.string().regex(/^[0-9a-f]{64}$/),
  parameter_count: z.number().int().nonnegative().nullable(),
  files: z.array(z.object({
    path: z.string(),
    size_bytes: z.number().int().positive(),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
  })),
});

export type SelectedFile = FileManifestEntry & { blob?: File };

export const voteResultSchema = z.object({
  listing_id: z.string(),
  up: z.number().int().nonnegative(),
  down: z.number().int().nonnegative(),
  score: z.number().int(),
  mine: z.number().int().min(-1).max(1),
});
