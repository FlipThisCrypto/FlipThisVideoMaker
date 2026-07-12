import { useQuery } from "@tanstack/react-query";

import type { RenderProfileCatalog } from "../types";
import { api } from "./client";

export const renderProfilesQueryKey = ["render-profiles"] as const;

export function getRenderProfiles() {
  return api<RenderProfileCatalog>("/render-profiles");
}

export function useRenderProfiles() {
  return useQuery({
    queryKey: renderProfilesQueryKey,
    queryFn: getRenderProfiles,
    staleTime: 60_000,
  });
}
