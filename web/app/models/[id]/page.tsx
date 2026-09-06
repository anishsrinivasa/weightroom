import { ModelDetail } from "./ModelDetail";

export default async function ModelDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ModelDetail id={id} />;
}
