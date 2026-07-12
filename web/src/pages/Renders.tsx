import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Render } from "../types";

export function Renders() {
  const { data = [] } = useQuery({
    queryKey: ["renders"],
    queryFn: () => api<Render[]>("/renders"),
  });
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Renders</h1>
      {data.length === 0 && <p className="card">No completed renders yet.</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {data.map((render) => (
          <article className="card" key={render.id}>
            <h2 className="font-bold">{render.render_profile} render</h2>
            <p className="mb-4 text-sm text-slate-400">
              {render.resolution} · {render.frame_rate}fps · {render.codec}
            </p>
            <a
              className="button inline-block"
              href={`/api/v1/renders/${render.id}`}
            >
              Open MP4
            </a>
          </article>
        ))}
      </div>
    </>
  );
}
