export function Placeholder({ title }: { title: string }) {
  return (
    <>
      <h1 className="text-3xl font-bold">{title}</h1>
      <p className="mt-3 text-slate-400">
        Controls become available when a project has generated assets.
      </p>
    </>
  );
}
