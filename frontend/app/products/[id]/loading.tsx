export default function ProductDetailLoading() {
  return (
    <section className="mx-auto max-w-3xl px-4 py-10">
      <div className="grid gap-6 sm:grid-cols-2">
        <div className="aspect-square animate-pulse rounded-xl bg-muted" />
        <div className="space-y-4">
          <div className="h-8 w-2/3 animate-pulse rounded bg-muted" />
          <div className="h-7 w-36 animate-pulse rounded bg-muted" />
          <div className="space-y-2">
            <div className="h-4 w-full animate-pulse rounded bg-muted" />
            <div className="h-4 w-5/6 animate-pulse rounded bg-muted" />
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-10 w-full max-w-40 animate-pulse rounded bg-muted" />
        </div>
      </div>
    </section>
  );
}
