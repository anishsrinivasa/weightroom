export function ErrorPanel({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-panel" role="alert">
      <div>
        <strong>We couldn&apos;t load this view.</strong>
        <p>{message}</p>
      </div>
      {retry ? (
        <button className="button" type="button" onClick={retry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function LoadingBlock({ label = "Loading…" }: { label?: string }) {
  return <div className="loading-block" aria-live="polite">{label}</div>;
}
