import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useRenderProfiles } from "../api/renderProfiles";
import { RenderProfileSelect } from "../components/RenderProfileSelect";
import type { Project } from "../types";
export function Projects() {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [resolutionProfile, setResolutionProfile] = useState("");
  const profileCatalog = useRenderProfiles();
  const { data = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
  });
  const create = useMutation({
    mutationFn: () =>
      api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name,
          description: "",
          target_duration: 30,
          aspect_ratio: "16:9",
          resolution_profile: resolutionProfile,
          fps: 24,
          global_visual_style: "",
          global_negative_prompt: "",
        }),
      }),
    onSuccess: () => {
      setName("");
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  useEffect(() => {
    if (!profileCatalog.data || resolutionProfile) return;
    setResolutionProfile(profileCatalog.data.default_profile);
  }, [profileCatalog.data, resolutionProfile]);
  const configuredProfiles = profileCatalog.data?.profiles ?? [];
  const resolutionProfileIsConfigured = configuredProfiles.some(
    (profile) => profile.name === resolutionProfile,
  );
  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim() && resolutionProfileIsConfigured) create.mutate();
  }
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Projects</h1>
      <form
        className="card mb-6 grid gap-3 md:grid-cols-[1fr_1fr_auto]"
        onSubmit={submit}
      >
        <label>
          <span className="text-sm">Project name</span>
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New project name"
          />
        </label>
        <RenderProfileSelect
          label="Default render profile"
          profiles={configuredProfiles}
          value={resolutionProfile}
          onChange={setResolutionProfile}
          disabled={
            profileCatalog.isPending ||
            profileCatalog.isError ||
            create.isPending
          }
        />
        <button
          className="button self-end"
          disabled={create.isPending || !resolutionProfileIsConfigured}
        >
          Create project
        </button>
        {profileCatalog.isError && (
          <p className="text-sm text-red-300 md:col-span-3" role="alert">
            Render profiles could not be loaded. Check the API before creating a
            project.
          </p>
        )}
        {create.isError && (
          <p className="text-sm text-red-300 md:col-span-3" role="alert">
            Project creation failed: {create.error.message}
          </p>
        )}
      </form>
      <div className="grid gap-4 md:grid-cols-2">
        {data.map((project) => (
          <Link
            className="card hover:border-accent"
            to={`/projects/${project.id}`}
            key={project.id}
          >
            <h2 className="text-xl font-bold">{project.name}</h2>
            <p className="text-slate-400">
              {project.status} · {project.target_duration}s ·{" "}
              {project.resolution_profile}
            </p>
          </Link>
        ))}
      </div>
    </>
  );
}
