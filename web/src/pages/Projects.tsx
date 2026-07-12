import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Project } from "../types";
export function Projects() {
  const client = useQueryClient();
  const [name, setName] = useState("");
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
          resolution_profile: "draft",
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
  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) create.mutate();
  }
  return (
    <>
      <h1 className="mb-6 text-3xl font-bold">Projects</h1>
      <form className="card mb-6 flex gap-3" onSubmit={submit}>
        <label className="flex-1">
          <span className="sr-only">Project name</span>
          <input
            className="field"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New project name"
          />
        </label>
        <button className="button" disabled={create.isPending}>
          Create project
        </button>
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
