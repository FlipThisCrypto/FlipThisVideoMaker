import { api } from "./client";
import type { ProjectRenderRequest } from "../types";

interface QueuedJob {
  id: string;
  state: string;
}

export function enqueueProjectRender(
  projectId: string,
  request: ProjectRenderRequest,
) {
  return api<QueuedJob>(`/projects/${projectId}/render`, {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function enqueueShotRegeneration(
  shotId: string,
  sameSeed: boolean,
  renderProfile: string,
) {
  return api<QueuedJob>(`/shots/${shotId}/regenerate`, {
    method: "POST",
    body: JSON.stringify({
      same_seed: sameSeed,
      render_profile: renderProfile,
    }),
  });
}
