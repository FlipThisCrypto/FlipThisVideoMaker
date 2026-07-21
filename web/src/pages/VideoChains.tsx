import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import {
  assembleVideoChain,
  changeVideoChainState,
  createVideoChain,
  createVideoChainClip,
  publishVideoChainStream,
  retryVideoChainClip,
  reviewVideoChainClip,
} from "../api/videoChains";
import type {
  Asset,
  Project,
  ProviderHealth,
  ProviderInfo,
  VideoChain,
  VideoChainClip,
} from "../types";

type UploadRole = "start" | "target" | "audio";
type LipSyncEligibility =
  | "speaking_face_visible"
  | "narration_no_visible_speaker"
  | "mouth_hidden"
  | "multiple_faces"
  | "no_speech"
  | "explicit_skip";

function assetUrl(assetId: string | null) {
  return assetId ? `/api/v1/assets/${assetId}/file` : "";
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Unknown error";
}

export function VideoChains() {
  const { id: projectId } = useParams();
  const client = useQueryClient();
  const [selectedChainId, setSelectedChainId] = useState("");
  const [chainName, setChainName] = useState("Continuous video sequence");
  const [continuationMode, setContinuationMode] = useState("planned_target");
  const [startAssetId, setStartAssetId] = useState("");
  const [targetAssetId, setTargetAssetId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [cameraDirection, setCameraDirection] = useState("natural");
  const [providerId, setProviderId] = useState("ltx-video-pro");
  const [renderProfile, setRenderProfile] = useState("final");
  const [gpuAssignment, setGpuAssignment] = useState<"gpu0" | "gpu1">(
    "gpu0",
  );
  const [branchPredecessorId, setBranchPredecessorId] = useState("");
  const [audioAssetId, setAudioAssetId] = useState("");
  const [lipSyncEligibility, setLipSyncEligibility] =
    useState<LipSyncEligibility>("explicit_skip");
  const [speakerLabel, setSpeakerLabel] = useState("");

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => api<Project>(`/projects/${projectId}`),
  });
  const chains = useQuery({
    queryKey: ["video-chains", projectId],
    queryFn: () => api<VideoChain[]>(`/projects/${projectId}/video-chains`),
  });
  const assets = useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => api<Asset[]>(`/projects/${projectId}/assets`),
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api<ProviderInfo[]>("/providers"),
  });
  const providerHealth = useQuery({
    queryKey: ["provider-health"],
    queryFn: () => api<ProviderHealth[]>("/providers/health"),
    refetchInterval: 15_000,
  });
  const clips = useQuery({
    queryKey: ["video-chain-clips", selectedChainId],
    queryFn: () =>
      api<VideoChainClip[]>(`/video-chains/${selectedChainId}/clips`),
    enabled: Boolean(selectedChainId),
    refetchInterval: 2_000,
  });

  useEffect(() => {
    if (!selectedChainId && chains.data?.[0]) {
      setSelectedChainId(chains.data[0].id);
    }
  }, [chains.data, selectedChainId]);

  const currentChain = chains.data?.find(
    (chain) => chain.id === selectedChainId,
  );
  const imageAssets = (assets.data ?? []).filter((asset) =>
    ["image/png", "image/jpeg"].includes(asset.mime_type),
  );
  const audioAssets = (assets.data ?? []).filter((asset) =>
    ["audio/wav", "audio/mpeg"].includes(asset.mime_type),
  );
  const generationProviders = (providers.data ?? []).filter(
    (provider) =>
      provider.generation_category ===
      "first_last_frame_generative_video",
  );
  const selectedProvider = generationProviders.find(
    (provider) => provider.id === providerId,
  );
  const selectedHealth = providerHealth.data?.find(
    (health) => health.provider === providerId,
  );
  const rifeProvider = providers.data?.find(
    (provider) => provider.id === "rife-local",
  );
  const rifeHealth = providerHealth.data?.find(
    (health) => health.provider === "rife-local",
  );
  const latentSyncProvider = providers.data?.find(
    (provider) => provider.id === "latentsync-local",
  );
  const latentSyncHealth = providerHealth.data?.find(
    (health) => health.provider === "latentsync-local",
  );
  const lipSyncRequested = lipSyncEligibility === "speaking_face_visible";
  const activeClips = useMemo(() => {
    const all = clips.data ?? [];
    const byId = new Map(all.map((clip) => [clip.id, clip]));
    let current = all
      .filter(
        (clip) => clip.lineage_version === currentChain?.active_lineage_version,
      )
      .sort((left, right) => left.sequence_number - right.sequence_number)
      .at(-1);
    const path: VideoChainClip[] = [];
    const seen = new Set<string>();
    while (current && !seen.has(current.id)) {
      seen.add(current.id);
      path.unshift(current);
      current = current.predecessor_clip_id
        ? byId.get(current.predecessor_clip_id)
        : undefined;
    }
    return path;
  }, [clips.data, currentChain?.active_lineage_version]);
  const acceptedClips = activeClips.filter(
    (clip) => clip.state === "accepted",
  );
  const activeTail = activeClips.at(-1);
  const latestAccepted = acceptedClips.at(-1);
  const branchPredecessor = (clips.data ?? []).find(
    (clip) => clip.id === branchPredecessorId,
  );
  const predecessor = branchPredecessor ?? latestAccepted;
  const resolvedStartAssetId =
    predecessor?.actual_last_frame_asset_id ?? startAssetId;
  const unsupportedReasons = useMemo(() => {
    const reasons: string[] = [];
    if (!selectedProvider) reasons.push("Select a true first/last-frame provider.");
    else if (!selectedProvider.available)
      reasons.push(`${selectedProvider.name} is disabled or its credential is missing.`);
    if (selectedHealth?.ok !== true)
      reasons.push("The generation provider has not passed its authenticated health probe.");
    if (!rifeProvider?.available)
      reasons.push("The external Practical-RIFE runtime or weights are unavailable.");
    if (rifeHealth?.ok !== true)
      reasons.push("RIFE has not passed its runtime health check.");
    if (!resolvedStartAssetId) reasons.push("Choose or upload a starting image.");
    if (!targetAssetId) reasons.push("Choose or upload a target ending image.");
    if (resolvedStartAssetId === targetAssetId)
      reasons.push("Start and ending Assets must differ.");
    if (!prompt.trim()) reasons.push("Describe continuous subject and camera motion.");
    if (currentChain?.state === "paused") reasons.push("Resume the chain before extending it.");
    if (!branchPredecessor && activeTail && activeTail.state !== "accepted")
      reasons.push("Accept, reject, cancel, or finish the active tail before adding a successor.");
    if (lipSyncRequested && !audioAssetId)
      reasons.push("Choose or upload a 10-second dialogue audio Asset.");
    if (lipSyncRequested && !latentSyncProvider?.available)
      reasons.push("The external LatentSync 1.5 runtime or weights are unavailable.");
    if (lipSyncRequested && latentSyncHealth?.ok !== true)
      reasons.push("LatentSync has not passed its runtime and checkpoint health check.");
    return reasons;
  }, [
    currentChain?.state,
    audioAssetId,
    activeTail,
    branchPredecessor,
    latentSyncHealth?.ok,
    latentSyncProvider?.available,
    lipSyncRequested,
    prompt,
    resolvedStartAssetId,
    rifeHealth?.ok,
    rifeProvider?.available,
    selectedHealth?.ok,
    selectedProvider,
    targetAssetId,
  ]);

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: ["video-chains", projectId] });
    void client.invalidateQueries({
      queryKey: ["video-chain-clips", selectedChainId],
    });
    void client.invalidateQueries({ queryKey: ["jobs"] });
    void client.invalidateQueries({ queryKey: ["assets", projectId] });
  };
  const createChainMutation = useMutation({
    mutationFn: () =>
      createVideoChain(projectId!, {
        name: chainName,
        description: "First/last-frame conditioned continuous-motion chain",
        continuation_mode: continuationMode,
        buffer_target_seconds: 30,
      }),
    onSuccess: (chain) => {
      setSelectedChainId(chain.id);
      void client.invalidateQueries({ queryKey: ["video-chains", projectId] });
    },
  });
  const upload = useMutation({
    mutationFn: async ({ file, role }: { file: File; role: UploadRole }) => {
      const form = new FormData();
      form.append("file", file);
      const asset = await api<Asset>(`/projects/${projectId}/assets`, {
        method: "POST",
        body: form,
      });
      return { asset, role };
    },
    onSuccess: ({ asset, role }) => {
      if (role === "start") setStartAssetId(asset.id);
      else if (role === "target") setTargetAssetId(asset.id);
      else setAudioAssetId(asset.id);
      void client.invalidateQueries({ queryKey: ["assets", projectId] });
    },
  });
  const generate = useMutation({
    mutationFn: () =>
      createVideoChainClip(selectedChainId, {
        predecessor_clip_id: predecessor?.id ?? null,
        regenerate_from_predecessor: Boolean(branchPredecessor),
        provider_id: providerId,
        provider_model: selectedProvider!.model_identity,
        start_frame_asset_id: resolvedStartAssetId,
        target_end_frame_asset_id: targetAssetId,
        prompt,
        camera_direction: cameraDirection,
        render_profile: renderProfile,
        gpu_assignment: gpuAssignment,
        interpolation_mode: "rife",
        interpolation_provider_id: "rife-local",
        audio_reference_asset_id: lipSyncRequested ? audioAssetId : null,
        lip_sync_mode: lipSyncRequested ? "latentsync" : "skip",
        lip_sync_provider_id: lipSyncRequested ? "latentsync-local" : null,
        lip_sync_settings: {
          eligibility: lipSyncEligibility,
          speaker_label: speakerLabel.trim() || null,
          face_index: null,
        },
      }),
    onSuccess: () => {
      setBranchPredecessorId("");
      setTargetAssetId("");
      invalidate();
    },
  });
  const review = useMutation({
    mutationFn: ({ clipId, operation }: { clipId: string; operation: "accept" | "reject" }) =>
      reviewVideoChainClip(clipId, operation),
    onSuccess: invalidate,
  });
  const retryClip = useMutation({
    mutationFn: retryVideoChainClip,
    onSuccess: invalidate,
  });
  const assemble = useMutation({
    mutationFn: () => assembleVideoChain(selectedChainId),
    onSuccess: invalidate,
  });
  const publish = useMutation({
    mutationFn: () => publishVideoChainStream(selectedChainId),
    onSuccess: invalidate,
  });
  const chainState = useMutation({
    mutationFn: (operation: "pause" | "resume" | "cancel") =>
      changeVideoChainState(selectedChainId, operation),
    onSuccess: invalidate,
  });

  if (!project.data) return <p>Loading project…</p>;
  return (
    <>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold">Continuous video chains</h1>
          <p className="text-slate-400">
            {project.data.name} · true first/last-frame generation · exact
            10-second, 60-fps delivery
          </p>
        </div>
        <Link className="button" to={`/projects/${projectId}`}>
          Back to project
        </Link>
      </div>

      <section className="card mb-5">
        <h2 className="text-xl font-bold">Create or select a chain</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <label>
            <span className="label">Existing chain</span>
            <select
              className="field"
              value={selectedChainId}
              onChange={(event) => setSelectedChainId(event.target.value)}
            >
              <option value="">Create one below</option>
              {(chains.data ?? []).map((chain) => (
                <option key={chain.id} value={chain.id}>
                  {chain.name} · {chain.state}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="label">New chain name</span>
            <input
              className="field"
              value={chainName}
              onChange={(event) => setChainName(event.target.value)}
            />
          </label>
          <label>
            <span className="label">Continuation mode</span>
            <select
              className="field"
              value={continuationMode}
              onChange={(event) => setContinuationMode(event.target.value)}
            >
              <option value="planned_target">Planned target frames</option>
              <option value="manual_target">Choose each target manually</option>
              <option value="auto_generate_target" disabled>
                Auto-generate target (prepared, not configured)
              </option>
            </select>
          </label>
        </div>
        <button
          className="button mt-3"
          disabled={!chainName.trim() || createChainMutation.isPending}
          onClick={() => createChainMutation.mutate()}
        >
          Create video chain
        </button>
        {createChainMutation.isError && (
          <p className="mt-2 text-red-300" role="alert">
            {errorText(createChainMutation.error)}
          </p>
        )}
      </section>

      {currentChain && (
        <>
          <section className="card mb-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-bold">{currentChain.name}</h2>
                <p className="text-sm text-slate-400">
                  {currentChain.state} · active lineage {currentChain.active_lineage_version}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {currentChain.state === "active" && (
                  <button className="button" onClick={() => chainState.mutate("pause")}>
                    Pause planning
                  </button>
                )}
                {currentChain.state === "paused" && (
                  <button className="button" onClick={() => chainState.mutate("resume")}>
                    Resume
                  </button>
                )}
                {!['complete', 'cancelled'].includes(currentChain.state) && (
                  <button className="button" onClick={() => chainState.mutate("cancel")}>
                    Cancel chain
                  </button>
                )}
              </div>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <button
                className="button"
                disabled={acceptedClips.length === 0 || assemble.isPending}
                onClick={() => assemble.mutate()}
              >
                Assemble accepted clips
              </button>
              <button
                className="button"
                disabled={acceptedClips.length === 0 || publish.isPending}
                onClick={() => publish.mutate()}
              >
                Publish validated HLS buffer
              </button>
              {currentChain.playlist_asset_id && (
                <a
                  className="button"
                  href={`/api/v1/video-chains/${currentChain.id}/hls/playlist.m3u8`}
                >
                  Open current playlist
                </a>
              )}
            </div>
            {Object.keys(currentChain.stream_state).length > 0 && (
              <details className="mt-3">
                <summary>Buffer and throughput evidence</summary>
                <pre className="mt-2 overflow-auto text-xs">
                  {JSON.stringify(currentChain.stream_state, null, 2)}
                </pre>
              </details>
            )}
          </section>

          <section className="card mb-5">
            <h2 className="text-xl font-bold">Generate the next clip</h2>
            <p className="mt-1 text-sm text-slate-400">
              The server will condition the selected production model on both images, preserve its native
              24-fps output, run RIFE, then prove exactly 600 displayed frames at 60 fps.
              Optional LatentSync remains a separate 25-fps performance stage and is
              revalidated against both boundary frames before review.
            </p>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div className="space-y-3">
                <label>
                  <span className="label">Generation provider</span>
                  <select
                    className="field"
                    value={providerId}
                    onChange={(event) => {
                      const nextProvider = event.target.value;
                      setProviderId(nextProvider);
                      if (nextProvider.startsWith("ltx-")) setRenderProfile("final");
                    }}
                  >
                    {generationProviders.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name}
                      </option>
                    ))}
                    {generationProviders.length === 0 && (
                      <option value="ltx-video-pro">LTX-2.3 Pro (not configured)</option>
                    )}
                  </select>
                </label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label>
                    <span className="label">Native quality</span>
                    <select
                      className="field"
                      value={renderProfile}
                      onChange={(event) => setRenderProfile(event.target.value)}
                    >
                      <option value="standard" disabled={providerId.startsWith("ltx-")}>
                        720p · 24 fps native · Luma only
                      </option>
                      <option value="final">1080p · 24 fps native</option>
                    </select>
                  </label>
                  <label>
                    <span className="label">RIFE GPU queue</span>
                    <select
                      className="field"
                      value={gpuAssignment}
                      onChange={(event) =>
                        setGpuAssignment(event.target.value as "gpu0" | "gpu1")
                      }
                    >
                      <option value="gpu0">GPU 0 · independent 12 GB</option>
                      <option value="gpu1">GPU 1 · independent 12 GB</option>
                    </select>
                  </label>
                </div>
                <label>
                  <span className="label">Branch from an earlier accepted clip</span>
                  <select
                    className="field"
                    value={branchPredecessorId}
                    onChange={(event) => setBranchPredecessorId(event.target.value)}
                  >
                    <option value="">Continue the active accepted tail</option>
                    {(clips.data ?? [])
                      .filter((clip) => clip.state === "accepted")
                      .map((clip) => (
                        <option key={clip.id} value={clip.id}>
                          Clip {clip.sequence_number} · lineage {clip.lineage_version}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  <span className="label">Continuous motion and scene prompt</span>
                  <textarea
                    className="field min-h-28"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    placeholder="Describe subject motion, environmental motion, temporal continuity, and the natural progression toward the target frame."
                  />
                </label>
                <label>
                  <span className="label">Camera direction</span>
                  <input
                    className="field"
                    value={cameraDirection}
                    onChange={(event) => setCameraDirection(event.target.value)}
                    placeholder="slow handheld tracking shot"
                  />
                </label>
              </div>

              <div className="space-y-3">
                <FrameAssetControl
                  label="Required starting frame"
                  assets={imageAssets}
                  value={resolvedStartAssetId}
                  onChange={setStartAssetId}
                  onUpload={(file) => upload.mutate({ file, role: "start" })}
                  locked={Boolean(predecessor)}
                  explanation={
                    predecessor
                      ? "Locked to the predecessor's actual decoded final frame."
                      : "Used as displayed frame 0 and measured after delivery encoding."
                  }
                />
                <FrameAssetControl
                  label="Target ending frame"
                  assets={imageAssets}
                  value={targetAssetId}
                  onChange={setTargetAssetId}
                  onUpload={(file) => upload.mutate({ file, role: "target" })}
                  locked={false}
                  explanation="Conditioned as the provider's final keyframe and measured at displayed frame 599."
                />
              </div>
            </div>

            <div className="mt-4 rounded border border-slate-700 p-4">
              <h3 className="font-bold">Optional dialogue and lip sync</h3>
              <p className="mt-1 text-sm text-slate-400">
                Classify the shot explicitly. Only one clearly visible speaking face is
                eligible for the implemented LatentSync 1.5 path; other cases skip the
                stage honestly.
              </p>
              <div className="mt-3 grid gap-3 lg:grid-cols-3">
                <label>
                  <span className="label">Speaking-shot eligibility</span>
                  <select
                    className="field"
                    value={lipSyncEligibility}
                    onChange={(event) =>
                      setLipSyncEligibility(event.target.value as LipSyncEligibility)
                    }
                  >
                    <option value="explicit_skip">Explicitly skip lip sync</option>
                    <option value="speaking_face_visible">
                      One visible speaking face · eligible
                    </option>
                    <option value="narration_no_visible_speaker">
                      Narration · no visible speaker
                    </option>
                    <option value="mouth_hidden">Speaker mouth hidden</option>
                    <option value="multiple_faces">
                      Multiple faces · deterministic selection unsupported
                    </option>
                    <option value="no_speech">No speech</option>
                  </select>
                </label>
                {lipSyncRequested && (
                  <>
                    <AudioAssetControl
                      assets={audioAssets}
                      value={audioAssetId}
                      onChange={setAudioAssetId}
                      onUpload={(file) => upload.mutate({ file, role: "audio" })}
                    />
                    <label>
                      <span className="label">Speaker label</span>
                      <input
                        className="field"
                        value={speakerLabel}
                        onChange={(event) => setSpeakerLabel(event.target.value)}
                        placeholder="Primary on-screen speaker"
                      />
                      <span className="mt-1 block text-xs text-slate-400">
                        Captured as intent; LatentSync does not expose a face-index control.
                      </span>
                    </label>
                  </>
                )}
              </div>
              {lipSyncRequested && (
                <p className="mt-3 text-xs text-slate-400">
                  LatentSync 1.5 · local GPU queue · official SyncNet confidence ≥3 and
                  AV offset within ±1 frame required · final audio and boundary QA required.
                </p>
              )}
            </div>

            <div className="mt-4 rounded border border-slate-700 p-3 text-sm">
              <p>
                Fixed delivery contract: <b>10.000 seconds · constant 60 fps · 600 frames</b>.
                The selected profile requests 24 native fps; the UI never labels the
                interpolated delivery frames as native AI frames.
              </p>
              {unsupportedReasons.length > 0 && (
                <ul className="mt-2 list-disc pl-5 text-amber-200">
                  {unsupportedReasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
            </div>
            <button
              className="button mt-3"
              disabled={unsupportedReasons.length > 0 || generate.isPending}
              onClick={() => generate.mutate()}
            >
              Generate true continuous-motion clip
            </button>
            {generate.isError && (
              <p className="mt-2 text-red-300" role="alert">
                {errorText(generate.error)}
              </p>
            )}
          </section>

          <section>
            <h2 className="mb-3 text-xl font-bold">Clip evidence and review</h2>
            <div className="space-y-4">
              {(clips.data ?? []).map((clip) => (
                <ClipEvidenceCard
                  key={clip.id}
                  clip={clip}
                  pending={review.isPending || retryClip.isPending}
                  onReview={(operation) =>
                    review.mutate({ clipId: clip.id, operation })
                  }
                  onRetry={() => retryClip.mutate(clip.id)}
                />
              ))}
              {(clips.data ?? []).length === 0 && (
                <p className="card text-slate-400">No clips have been queued.</p>
              )}
            </div>
          </section>
        </>
      )}
    </>
  );
}

function AudioAssetControl({
  assets,
  value,
  onChange,
  onUpload,
}: {
  assets: Asset[];
  value: string;
  onChange: (value: string) => void;
  onUpload: (file: File) => void;
}) {
  return (
    <div>
      <label>
        <span className="label">Dialogue audio Asset</span>
        <select
          className="field"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Select 10-second audio</option>
          {assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.type} · {asset.duration?.toFixed(2) ?? "?"}s · {asset.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <label className="button mt-2 inline-block cursor-pointer">
        Upload dialogue audio
        <input
          className="sr-only"
          type="file"
          accept="audio/wav,audio/mpeg"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
          }}
        />
      </label>
    </div>
  );
}

function FrameAssetControl({
  label,
  assets,
  value,
  onChange,
  onUpload,
  locked,
  explanation,
}: {
  label: string;
  assets: Asset[];
  value: string;
  onChange: (value: string) => void;
  onUpload: (file: File) => void;
  locked: boolean;
  explanation: string;
}) {
  return (
    <div>
      <label>
        <span className="label">{label}</span>
        <select
          className="field"
          value={value}
          disabled={locked}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Select an image Asset</option>
          {assets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.type} · {asset.width}×{asset.height} · {asset.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </label>
      <p className="mt-1 text-xs text-slate-400">{explanation}</p>
      {!locked && (
        <label className="button mt-2 inline-block cursor-pointer">
          Upload image
          <input
            className="sr-only"
            type="file"
            accept="image/png,image/jpeg"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onUpload(file);
            }}
          />
        </label>
      )}
      {value && (
        <img
          className="mt-2 max-h-48 w-full rounded object-contain bg-black"
          src={assetUrl(value)}
          alt={`${label} preview`}
        />
      )}
    </div>
  );
}

function ClipEvidenceCard({
  clip,
  pending,
  onReview,
  onRetry,
}: {
  clip: VideoChainClip;
  pending: boolean;
  onReview: (operation: "accept" | "reject") => void;
  onRetry: () => void;
}) {
  const reviewable = ["awaiting_review", "degraded"].includes(clip.state);
  return (
    <article className="card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-bold">
            Clip {clip.sequence_number} · lineage {clip.lineage_version} · revision {clip.revision}
          </h3>
          <p className="text-sm text-slate-400">
            {clip.request_snapshot.provider_id} / {clip.request_snapshot.provider_model} ·{" "}
            {clip.request_snapshot.native_requested_fps} fps native request →{" "}
            {clip.request_snapshot.delivery_fps} fps delivery · motion: first/last-frame
            generative · performance: {clip.request_snapshot.lip_sync_mode}
          </p>
        </div>
        <span className="rounded bg-slate-800 px-3 py-1 text-sm">{clip.state}</span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <BoundaryImage label="Requested start" assetId={clip.planned_start_frame_asset_id} />
        <BoundaryImage label="Actual displayed start" assetId={clip.actual_start_frame_asset_id} />
        <BoundaryImage label="Requested target end" assetId={clip.target_end_frame_asset_id} />
        <BoundaryImage label="Actual displayed end" assetId={clip.actual_last_frame_asset_id} />
      </div>
      {clip.delivery_video_asset_id && (
        <video
          className="mt-4 max-h-96 w-full rounded bg-black"
          controls
          src={assetUrl(clip.delivery_video_asset_id)}
        />
      )}
      {clip.provider_warnings.length > 0 && (
        <ul className="mt-3 list-disc pl-5 text-sm text-amber-200">
          {clip.provider_warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {clip.job_id && (
          <Link className="button" to="/jobs">
            Inspect Job
          </Link>
        )}
        {reviewable && clip.state !== "degraded" && (
          <button className="button" disabled={pending} onClick={() => onReview("accept")}>
            Accept and use actual end next
          </button>
        )}
        {reviewable && (
          <button className="button" disabled={pending} onClick={() => onReview("reject")}>
            Reject
          </button>
        )}
        {["failed", "cancelled"].includes(clip.state) && (
          <button className="button" disabled={pending} onClick={onRetry}>
            Retry from persisted stages
          </button>
        )}
      </div>
      {clip.state === "degraded" && (
        <p className="mt-3 text-sm text-red-300" role="alert">
          This output failed the production continuity threshold and cannot be accepted.
        </p>
      )}
      <details className="mt-3">
        <summary>Immutable request, provenance, and QA</summary>
        <pre className="mt-2 max-h-96 overflow-auto text-xs">
          {JSON.stringify(
            {
              request_digest: clip.request_digest,
              request: clip.request_snapshot,
              result: clip.result_snapshot,
              failure: clip.failure_info,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </article>
  );
}

function BoundaryImage({ label, assetId }: { label: string; assetId: string | null }) {
  return (
    <figure>
      <figcaption className="mb-1 text-xs text-slate-400">{label}</figcaption>
      {assetId ? (
        <img
          className="aspect-video w-full rounded bg-black object-contain"
          src={assetUrl(assetId)}
          alt={label}
        />
      ) : (
        <div className="flex aspect-video items-center justify-center rounded bg-slate-900 text-xs text-slate-500">
          Not available
        </div>
      )}
    </figure>
  );
}
