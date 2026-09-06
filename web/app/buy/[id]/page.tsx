import { BuyerModelDetail } from "./BuyerModelDetail";

export default async function BuyerModelDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <BuyerModelDetail id={id} />;
}
