export function BuyerModelDetail({ id }: { id: string }) {
  return (
    <section className="empty-state">
      <p className="eyebrow">Model listing</p>
      <h1>Loading buyer view</h1>
      <p className="mono">{id}</p>
    </section>
  );
}
