"use client";

import { ErrorPanel } from "@/components/AsyncState";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorPanel message="An unexpected interface error occurred." retry={reset} />;
}
