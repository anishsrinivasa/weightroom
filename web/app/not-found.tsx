import Link from "next/link";

export default function NotFound() {
  return (
    <div className="empty-state">
      <p className="eyebrow">404</p>
      <h1>Model not found.</h1>
      <p>It may have been withdrawn, or it may belong to another seller.</p>
      <Link className="button primary" href="/models">Return to your models</Link>
    </div>
  );
}
