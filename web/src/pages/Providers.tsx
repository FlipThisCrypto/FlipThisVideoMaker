import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
export function Providers() {
  const { data = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: () =>
      api<
        {
          id: string;
          name: string;
          model_identity: string;
          available: boolean;
          capabilities: string[];
        }[]
      >("/providers"),
  });
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Provider discovery</h1>
      <div className="grid gap-4 md:grid-cols-2">
        {data.map((p) => (
          <article className="card" key={p.id}>
            <h2 className="font-bold">{p.name}</h2>
            <p className="text-sm text-slate-400">
              {p.model_identity} · {p.available ? "available" : "offline"}
            </p>
            <p className="mt-2">{p.capabilities.join(", ")}</p>
          </article>
        ))}
      </div>
    </>
  );
}
