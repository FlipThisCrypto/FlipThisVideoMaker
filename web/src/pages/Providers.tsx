import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ProviderHealth, ProviderInfo } from "../types";
export function Providers() {
  const { data = [] } = useQuery({
    queryKey: ["providers"],
    queryFn: () => api<ProviderInfo[]>("/providers"),
  });
  const health = useQuery({
    queryKey: ["provider-health"],
    queryFn: () => api<ProviderHealth[]>("/providers/health"),
  });
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Provider discovery</h1>
      <div className="grid gap-4 md:grid-cols-2">
        {data.map((provider) => {
          const probe = health.data?.find(
            (item) => item.provider === provider.id,
          );
          return (
          <article className="card" key={provider.id}>
            <h2 className="font-bold">{provider.name}</h2>
            <p className="text-sm text-slate-400">
              {provider.model_identity} · configured capability{" "}
              {provider.available ? "available" : "unavailable"} · health{" "}
              {probe?.ok ? "passed" : probe?.status ?? "not checked"}
            </p>
            <p className="mt-2">{provider.capabilities.join(", ") || "No advertised capability"}</p>
            {provider.generation_category && (
              <p className="mt-2 text-sm">
                Generation category: <code>{provider.generation_category}</code>
              </p>
            )}
            <p className="mt-2 text-sm text-slate-300">{provider.notes}</p>
            {provider.generation_category ===
              "first_last_frame_generative_video" && (
              <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <dt className="text-slate-400">Native FPS</dt>
                  <dd>{provider.native_frame_rates.join(", ")}</dd>
                </div>
                <div>
                  <dt className="text-slate-400">Durations</dt>
                  <dd>{provider.supported_durations_seconds.join(", ")} seconds</dd>
                </div>
                <div>
                  <dt className="text-slate-400">Remote cancellation</dt>
                  <dd>{provider.cancellation_supported ? "supported" : "not supported"}</dd>
                </div>
                <div>
                  <dt className="text-slate-400">Percentage progress</dt>
                  <dd>{provider.progress_supported ? "supported" : "state only"}</dd>
                </div>
              </dl>
            )}
          </article>
          );
        })}
      </div>
    </>
  );
}
