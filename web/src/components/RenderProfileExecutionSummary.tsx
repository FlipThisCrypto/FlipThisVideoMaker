import type { RenderProfileExecution } from "../types";

export function RenderProfileExecutionSummary({
  execution,
}: {
  execution: RenderProfileExecution;
}) {
  const changed = execution.requested_profile !== execution.effective_profile;
  return (
    <p className="text-sm text-slate-400">
      Render profile: {execution.requested_profile}
      {changed && (
        <>
          {" "}→ {execution.effective_profile} after {execution.fallback_history.length}
          {" "}safe fallback
          {execution.fallback_history.length === 1 ? "" : "s"}
        </>
      )}
    </p>
  );
}
