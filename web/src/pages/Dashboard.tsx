import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job, Project, WorkerStatus } from "../types";
export function Dashboard() {
  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
  });
  const { data: jobs = [] } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    refetchInterval: 2000,
  });
  const { data: gpus = [] } = useQuery({
    queryKey: ["gpus"],
    queryFn: () => api<Record<string, unknown>[]>("/gpus"),
  });
  const { data: workers = [] } = useQuery({
    queryKey: ["workers"],
    queryFn: () => api<WorkerStatus[]>("/workers"),
    refetchInterval: 5000,
  });
  const onlineWorkers = workers.filter((worker) => worker.online).length;
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Production dashboard</h1>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <section className="card">
          <h2 className="text-lg font-bold">Projects</h2>
          <p className="mt-3 text-4xl text-accent">{projects.length}</p>
        </section>
        <section className="card">
          <h2 className="text-lg font-bold">Active jobs</h2>
          <p className="mt-3 text-4xl text-accent">
            {jobs.filter((j) => ["queued", "running"].includes(j.state)).length}
          </p>
        </section>
        <section className="card">
          <h2 className="text-lg font-bold">Visible GPUs</h2>
          <p className="mt-3 text-4xl text-accent">{gpus.length}</p>
          <p className="text-sm text-slate-400">CPU-only mode is supported</p>
        </section>
        <section className="card">
          <h2 className="text-lg font-bold">Workers online</h2>
          <p className="mt-3 text-4xl text-accent">
            {onlineWorkers}/{workers.filter((worker) => worker.configured).length}
          </p>
          <p className="text-sm text-slate-400">
            {workers.some((worker) => worker.runtime_state === "busy")
              ? "A worker is processing a job"
              : "No worker is currently busy"}
          </p>
        </section>
      </div>
    </>
  );
}
