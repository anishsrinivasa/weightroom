import { redirect } from "next/navigation";

export default async function LegacyModelDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/sell/models/${encodeURIComponent(id)}`);
}
