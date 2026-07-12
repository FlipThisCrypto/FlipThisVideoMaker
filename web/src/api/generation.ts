import { api } from "./client";

interface QueuedJob {
  id: string;
  state: string;
}

export function enqueueProjectRender(projectId: string, renderProfile: string) {
  return api<QueuedJob>(`/projects/${projectId}/render`, {
    method: "POST",
    body: JSON.stringify({ render_profile: renderProfile }),
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
