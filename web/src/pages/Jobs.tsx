import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { RenderProfileExecutionSummary } from "../components/RenderProfileExecutionSummary";
import type { Job } from "../types";
export function Jobs() {
  const client = useQueryClient();
  const { data = [] } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    refetchInterval: 1500,
  });
  const action = useMutation({
    mutationFn: ({
      id,
      operation,
    }: {
      id: string;
      operation: "cancel" | "retry";
    }) => api(`/jobs/${id}/${operation}`, { method: "POST" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["jobs"] }),
  });
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Job queue</h1>
      <div className="space-y-3">
        {data.map((job) => (
          <article className="card" key={job.id}>
            <div className="flex justify-between">
              <b>{job.job_type}</b>
              <span>{job.state}</span>
            </div>
            <progress className="mt-3 w-full" max={1} value={job.progress} />
            <p className="text-sm text-slate-400">
              {job.current_stage} · {job.gpu_assignment} · attempt{" "}
              {job.attempt_number}
            </p>
            {job.render_profile_execution && (
              <RenderProfileExecutionSummary
                execution={job.render_profile_execution}
              />
            )}
            {job.render_profile_execution_error && (
              <p className="mt-2 text-sm text-red-300" role="alert">
                This job has an invalid render-profile snapshot and cannot run
                until it is replaced or repaired.
              </p>
            )}
            <div className="mt-3 flex gap-2">
              {job.log_path && (
                <a className="button" href={`/api/v1/jobs/${job.id}/log`}>
                  Download log
                </a>
              )}
              {["queued", "running", "cancel_requested"].includes(
                job.state,
              ) && (
                <button
                  className="button"
                  disabled={
                    action.isPending || job.state === "cancel_requested"
                  }
                  onClick={() =>
                    action.mutate({ id: job.id, operation: "cancel" })
                  }
                >
                  Cancel
                </button>
              )}
              {["failed", "cancelled"].includes(job.state) && (
                <button
                  className="button"
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({ id: job.id, operation: "retry" })
                  }
                >
                  Retry
                </button>
              )}
            </div>
            {Object.keys(job.error_info).length > 0 && (
              <details className="mt-3">
                <summary>Error details</summary>
                <pre className="overflow-auto text-xs">
                  {JSON.stringify(job.error_info, null, 2)}
                </pre>
              </details>
            )}
          </article>
        ))}
      </div>
    </>
  );
}
