import asyncio
import hashlib
import ipaddress
import json
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from flipthis_video_maker.contracts.video_generation import GenerationCategory, utc_now
from flipthis_video_maker.media.video_delivery import inspect_frame_timing
from flipthis_video_maker.providers.base.errors import (
    ProviderCleanupResult,
    ProviderExecutionError,
    ProviderFailureKind,
    ProviderOutOfMemoryError,
)
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo
from flipthis_video_maker.providers.base.video_generation import (
    ProviderRunOutput,
    ResolvedFirstLastFrameRequest,
)

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]

MODEL_IDENTITY = "wan2.2-i2v-a14b-fp8"
HIGH_MODEL = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
LOW_MODEL = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
_ALLOWED_NODE_TYPES = {
    "UNETLoader",
    "ModelSamplingSD3",
    "CLIPLoader",
    "CLIPTextEncode",
    "VAELoader",
    "LoadImage",
    "WanFirstLastFrameToVideo",
    "KSamplerAdvanced",
    "VAEDecode",
    "CreateVideo",
    "SaveVideo",
}
_REQUIRED_PLACEHOLDERS = {
    "{{CFG}}",
    "{{END_IMAGE}}",
    "{{HEIGHT}}",
    "{{HIGH_NOISE_END_STEP}}",
    "{{LENGTH}}",
    "{{NATIVE_FPS}}",
    "{{NEGATIVE_PROMPT}}",
    "{{OUTPUT_PREFIX}}",
    "{{PROMPT}}",
    "{{SEED}}",
    "{{SHIFT}}",
    "{{START_IMAGE}}",
    "{{STEPS}}",
    "{{WIDTH}}",
}


class ComfyUIWanFirstLastFrameProvider:
    """Dedicated local ComfyUI/Wan2.2 native first/last-frame adapter."""

    def __init__(
        self,
        provider_id: str,
        *,
        endpoint: str,
        workflow_template: Path,
        gpu_assignment: str,
        model: str = MODEL_IDENTITY,
        timeout_seconds: float = 7200,
        poll_interval_seconds: float = 2,
        max_download_mb: int = 4096,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _validate_loopback_endpoint(endpoint)
        if gpu_assignment not in {"gpu0", "gpu1"}:
            raise ValueError("Wan FLF provider must own one configured GPU queue")
        if model != MODEL_IDENTITY:
            raise ValueError(f"Wan FLF adapter supports only {MODEL_IDENTITY}")
        self.provider_id = provider_id
        self.endpoint = endpoint.rstrip("/")
        self.workflow_template = workflow_template
        self.gpu_assignment = gpu_assignment
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_download_bytes = max_download_mb * 1024 * 1024
        self.transport = transport

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.provider_id,
            name=f"Local Wan2.2 FLF ({self.gpu_assignment})",
            model_identity=self.model,
            capabilities={
                Capability.VIDEO_GENERATION,
                Capability.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
            },
            available=self.workflow_template.is_file(),
            supported_inputs={
                "image/png",
                "image/jpeg",
                "prompt",
                "negative_prompt",
                "seed",
            },
            max_duration_seconds=10,
            max_width=848,
            max_height=480,
            native_frame_rates={8},
            supported_durations_seconds={10},
            generation_category=GenerationCategory.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
            cancellation_supported=True,
            progress_supported=True,
            notes=(
                "Apache-2.0 Wan2.2 I2V-A14B FP8 through a dedicated local ComfyUI instance. "
                "The graph uses native WanFirstLastFrameToVideo conditioning and never substitutes "
                "a transition. One endpoint owns exactly one physical-GPU queue."
            ),
        )

    async def health(self) -> dict[str, object]:
        try:
            template = self._load_and_validate_template()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return {
                "ok": False,
                "status": "invalid_workflow_template",
                "error_type": type(error).__name__,
            }
        try:
            async with self._client() as client:
                system = await self._json_get(client, "/system_stats")
                node_records = {
                    node: await self._json_get(client, f"/object_info/{node}")
                    for node in (
                        "WanFirstLastFrameToVideo",
                        "UNETLoader",
                        "CLIPLoader",
                        "VAELoader",
                        "SaveVideo",
                    )
                }
        except (httpx.HTTPError, ValueError) as error:
            return {
                "ok": False,
                "status": "unreachable_or_invalid",
                "error_type": type(error).__name__,
            }
        missing_nodes = [node for node, record in node_records.items() if node not in record]
        if missing_nodes:
            return {"ok": False, "status": "missing_nodes", "missing_nodes": missing_nodes}
        missing_models = self._missing_models(node_records)
        if missing_models:
            return {"ok": False, "status": "missing_models", "missing_models": missing_models}
        devices = system.get("devices")
        if not isinstance(devices, list) or len(devices) != 1:
            return {
                "ok": False,
                "status": "invalid_device_isolation",
                "visible_device_count": len(devices) if isinstance(devices, list) else None,
            }
        device = devices[0]
        if not isinstance(device, dict) or device.get("type") != "cuda":
            return {"ok": False, "status": "cuda_unavailable"}
        system_record = system.get("system")
        version = system_record.get("comfyui_version") if isinstance(system_record, dict) else None
        return {
            "ok": True,
            "status": "ready",
            "model": self.model,
            "gpu_assignment": self.gpu_assignment,
            "visible_device_count": 1,
            "device_name": device.get("name"),
            "comfyui_version": version,
            "workflow_sha256": _sha256(self.workflow_template),
            "workflow_contract_version": template["contract_version"],
        }

    async def generate(
        self,
        request: ResolvedFirstLastFrameRequest,
        *,
        cancel_requested: CancelCheck | None = None,
        progress: ProgressCallback | None = None,
    ) -> ProviderRunOutput:
        settings = self._validate_request(request)
        template = self._load_and_validate_template()
        token = uuid.uuid4().hex
        submitted_at = utc_now()
        started = time.monotonic()
        prompt_id: str | None = None
        partial = request.output_path.with_suffix(request.output_path.suffix + ".partial")
        try:
            async with self._client() as client:
                self._raise_if_cancelled(cancel_requested, prompt_id)
                start_name = await self._upload_image(
                    client,
                    request.start_frame_path,
                    request.start_frame_mime_type,
                    f"{token}-start{request.start_frame_path.suffix.lower()}",
                )
                end_name = await self._upload_image(
                    client,
                    request.end_frame_path,
                    request.end_frame_mime_type,
                    f"{token}-end{request.end_frame_path.suffix.lower()}",
                )
                if progress:
                    progress(0.05, "boundary_frames_uploaded_to_local_comfyui")
                graph = self._render_graph(
                    template,
                    request,
                    settings,
                    start_name=start_name,
                    end_name=end_name,
                    output_prefix=f"flipthis/{token}",
                )
                submission = await self._json_post(
                    client,
                    "/prompt",
                    {"prompt": graph, "client_id": f"flipthis-{token}"},
                )
                prompt_id = _required_string(submission, "prompt_id")
                if progress:
                    progress(0.1, "wan_flf_queued")
                output = await self._poll_for_output(
                    client,
                    prompt_id,
                    str(template["output_node_id"]),
                    cancel_requested=cancel_requested,
                    progress=progress,
                )
                provider_seconds = time.monotonic() - started
                download_started = time.monotonic()
                await self._download_output(
                    client,
                    output,
                    partial,
                    cancel_requested=cancel_requested,
                    prompt_id=prompt_id,
                )
                download_seconds = time.monotonic() - download_started
                self._validate_native_output(partial, request, prompt_id)
                if request.output_path.exists():
                    raise ValueError("Wan FLF output destination was created concurrently")
                partial.replace(request.output_path)
                free_succeeded = await self._free_models(client)
        except ProviderOutOfMemoryError:
            partial.unlink(missing_ok=True)
            raise
        except ProviderExecutionError:
            partial.unlink(missing_ok=True)
            raise
        except httpx.TimeoutException as error:
            partial.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="comfyui_http_timeout",
                provider_job_id=prompt_id,
            ) from error
        except (httpx.HTTPError, OSError, ValueError) as error:
            partial.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                retryable=isinstance(error, httpx.HTTPError),
                backend_code=type(error).__name__,
                provider_job_id=prompt_id,
            ) from error
        warnings: tuple[str, ...] = (
            "ComfyUI retains uniquely named uploaded boundary images in its local input directory",
        )
        if not free_succeeded:
            warnings += ("ComfyUI model-unload request did not complete",)
        return ProviderRunOutput(
            output_path=request.output_path,
            provider_job_id=prompt_id,
            actual_model=self.model,
            api_version="comfyui-api-v1",
            submitted_at=submitted_at,
            completed_at=utc_now(),
            provider_seconds=provider_seconds,
            download_seconds=download_seconds,
            actual_settings={
                **settings,
                "width": request.snapshot.width,
                "height": request.snapshot.height,
                "native_fps": request.snapshot.native_requested_fps,
                "native_frame_count": self._native_frame_count(request),
                "workflow_sha256": _sha256(self.workflow_template),
                "gpu_assignment": self.gpu_assignment,
            },
            warnings=warnings,
        )

    def _validate_native_output(
        self,
        path: Path,
        request: ResolvedFirstLastFrameRequest,
        prompt_id: str,
    ) -> None:
        facts = inspect_frame_timing(path)
        expected_frames = self._native_frame_count(request)
        valid = (
            facts["decoded_frame_count"] == expected_frames
            and abs(facts["average_frame_rate"] - request.snapshot.native_requested_fps) <= 0.001
            and facts["constant_frame_rate"]
            and facts["width"] == request.snapshot.width
            and facts["height"] == request.snapshot.height
        )
        if not valid:
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="validate_output",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="native_timing_or_resolution_mismatch",
                provider_job_id=prompt_id,
            )

    async def cleanup_after_oom(self, _error: ProviderOutOfMemoryError) -> ProviderCleanupResult:
        try:
            async with self._client() as client:
                await self._interrupt(client, None)
                completed = await self._free_models(client)
        except httpx.HTTPError:
            completed = False
        return ProviderCleanupResult(
            provider_id=self.provider_id,
            completed=completed,
            retry_safe=completed,
            action_code="comfyui.interrupt_and_free" if completed else "comfyui.cleanup_failed",
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.endpoint,
            timeout=httpx.Timeout(30, read=60),
            transport=self.transport,
            follow_redirects=False,
        )

    def _load_and_validate_template(self) -> dict[str, Any]:
        payload = json.loads(self.workflow_template.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Wan workflow template must be a JSON object")
        if payload.get("contract_version") != 1 or payload.get("model_identity") != self.model:
            raise ValueError("Wan workflow contract or model identity does not match")
        if payload.get("native_fps") != 8 or not isinstance(payload.get("output_node_id"), str):
            raise ValueError("Wan workflow native FPS or output node is invalid")
        graph = payload.get("prompt")
        if not isinstance(graph, dict) or not graph:
            raise ValueError("Wan workflow prompt graph is missing")
        node_types: set[str] = set()
        for node_id, node in graph.items():
            if not isinstance(node_id, str) or not isinstance(node, dict):
                raise ValueError("Wan workflow nodes must use string IDs and object values")
            class_type = node.get("class_type")
            inputs = node.get("inputs")
            if class_type not in _ALLOWED_NODE_TYPES or not isinstance(inputs, dict):
                raise ValueError("Wan workflow contains an unapproved node or invalid inputs")
            node_types.add(class_type)
            for value in inputs.values():
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and isinstance(value[0], str)
                    and value[0] not in graph
                ):
                    raise ValueError("Wan workflow contains a dangling node reference")
        if node_types != _ALLOWED_NODE_TYPES:
            raise ValueError("Wan workflow node allowlist does not match the production graph")
        if payload["output_node_id"] not in graph:
            raise ValueError("Wan workflow output node does not exist")
        placeholders = _collect_placeholders(payload)
        if placeholders != _REQUIRED_PLACEHOLDERS:
            raise ValueError("Wan workflow placeholders do not match the immutable renderer")
        serialized = json.dumps(graph, sort_keys=True)
        for required in (HIGH_MODEL, LOW_MODEL, TEXT_ENCODER, VAE):
            if required not in serialized:
                raise ValueError("Wan workflow model filenames do not match the pinned manifest")
        return payload

    def _validate_request(self, request: ResolvedFirstLastFrameRequest) -> dict[str, int | float]:
        snapshot = request.snapshot
        if request.output_path.exists() or request.output_path.suffix.lower() != ".mp4":
            raise ValueError("Wan FLF output must be a new MP4 path")
        if snapshot.provider_id != self.provider_id or snapshot.provider_model != self.model:
            raise ValueError("Request provider identity does not match the Wan FLF adapter")
        if snapshot.duration_seconds != 10 or snapshot.native_requested_fps != 8:
            raise ValueError("Local Wan 12 GB profile requires 10 seconds at requested 8 FPS")
        if (snapshot.width, snapshot.height) != (848, 480):
            raise ValueError("Local Wan 12 GB profile supports only 848x480")
        if snapshot.motion_strength is not None or snapshot.identity_reference_asset_ids:
            raise ValueError("Local Wan FLF does not expose motion strength or identity references")
        if snapshot.audio_reference_asset_id is not None:
            raise ValueError("Local Wan generation does not accept integrated audio conditioning")
        for path, mime in (
            (request.start_frame_path, request.start_frame_mime_type),
            (request.end_frame_path, request.end_frame_mime_type),
        ):
            if mime not in {"image/png", "image/jpeg"} or not path.is_file():
                raise ValueError("Wan boundary Assets must resolve to PNG or JPEG files")
        configured = snapshot.provider_settings.get(self.provider_id, {})
        unknown = set(configured) - {"cfg", "high_noise_end_step", "shift", "steps"}
        if unknown:
            raise ValueError("Unsupported Wan provider-specific settings")
        steps = _bounded_int(configured.get("steps", 20), "steps", 4, 50)
        high_end = _bounded_int(
            configured.get("high_noise_end_step", steps // 2),
            "high_noise_end_step",
            1,
            steps - 1,
        )
        cfg = _bounded_float(configured.get("cfg", 4), "cfg", 1, 10)
        shift = _bounded_float(configured.get("shift", 8), "shift", 0, 20)
        return {"steps": steps, "high_noise_end_step": high_end, "cfg": cfg, "shift": shift}

    def _render_graph(
        self,
        template: dict[str, Any],
        request: ResolvedFirstLastFrameRequest,
        settings: dict[str, int | float],
        *,
        start_name: str,
        end_name: str,
        output_prefix: str,
    ) -> dict[str, Any]:
        prompt = request.snapshot.prompt
        if request.snapshot.camera_direction != "natural":
            prompt = f"{prompt}\nCamera direction: {request.snapshot.camera_direction}"
        replacements: dict[str, object] = {
            "{{CFG}}": settings["cfg"],
            "{{END_IMAGE}}": end_name,
            "{{HEIGHT}}": request.snapshot.height,
            "{{HIGH_NOISE_END_STEP}}": settings["high_noise_end_step"],
            "{{LENGTH}}": self._native_frame_count(request),
            "{{NATIVE_FPS}}": request.snapshot.native_requested_fps,
            "{{NEGATIVE_PROMPT}}": request.snapshot.negative_prompt,
            "{{OUTPUT_PREFIX}}": output_prefix,
            "{{PROMPT}}": prompt,
            "{{SEED}}": request.snapshot.seed or 0,
            "{{SHIFT}}": settings["shift"],
            "{{START_IMAGE}}": start_name,
            "{{STEPS}}": settings["steps"],
            "{{WIDTH}}": request.snapshot.width,
        }
        return cast(
            dict[str, Any],
            _replace_placeholders(deepcopy(template["prompt"]), replacements),
        )

    def _native_frame_count(self, request: ResolvedFirstLastFrameRequest) -> int:
        requested = round(request.snapshot.duration_seconds * request.snapshot.native_requested_fps)
        return requested + ((1 - requested) % 4)

    async def _upload_image(
        self,
        client: httpx.AsyncClient,
        path: Path,
        mime_type: str,
        filename: str,
    ) -> str:
        with path.open("rb") as source:
            response = await client.post(
                "/upload/image",
                data={"type": "input", "subfolder": "flipthis", "overwrite": "false"},
                files={"image": (filename, source, mime_type)},
            )
        self._raise_http(response, "upload_boundary")
        payload = _json_object(response)
        name = _required_string(payload, "name")
        subfolder = payload.get("subfolder")
        record_type = payload.get("type")
        if subfolder != "flipthis" or record_type != "input" or Path(name).name != name:
            raise ValueError("ComfyUI returned an unsafe uploaded image location")
        return f"flipthis/{name}"

    async def _poll_for_output(
        self,
        client: httpx.AsyncClient,
        prompt_id: str,
        output_node_id: str,
        *,
        cancel_requested: CancelCheck | None,
        progress: ProgressCallback | None,
    ) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if cancel_requested is not None and cancel_requested():
                await self._interrupt(client, prompt_id)
                self._raise_if_cancelled(cancel_requested, prompt_id)
            history = await self._json_get(client, f"/history/{prompt_id}")
            record = history.get(prompt_id)
            if record is None:
                if progress:
                    progress(0.15, "wan_flf_waiting")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            if not isinstance(record, dict):
                raise ValueError("ComfyUI history record is invalid")
            oom_code = _structured_oom_code(record)
            if oom_code is not None:
                raise ProviderOutOfMemoryError(
                    provider_id=self.provider_id,
                    operation="generate_video",
                    backend_code=oom_code,
                )
            status = record.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                raise ProviderExecutionError(
                    provider_id=self.provider_id,
                    operation="generate_video",
                    backend_code="comfyui_execution_error",
                    provider_job_id=prompt_id,
                )
            output = _history_video_output(record, output_node_id)
            if output is not None:
                if progress:
                    progress(0.8, "wan_flf_native_video_ready")
                return output
            await asyncio.sleep(self.poll_interval_seconds)
        await self._interrupt(client, prompt_id)
        raise ProviderExecutionError(
            provider_id=self.provider_id,
            operation="generate_video",
            failure_kind=ProviderFailureKind.TIMEOUT,
            retryable=True,
            backend_code="comfyui_prompt_timeout",
            provider_job_id=prompt_id,
        )

    async def _download_output(
        self,
        client: httpx.AsyncClient,
        output: dict[str, str],
        partial: Path,
        *,
        cancel_requested: CancelCheck | None,
        prompt_id: str,
    ) -> None:
        partial.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        async with client.stream("GET", "/view", params=output) as response:
            self._raise_http(response, "download_output", prompt_id)
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if content_type not in {"video/mp4", "application/octet-stream"}:
                raise ValueError("ComfyUI output response is not an MP4 stream")
            with partial.open("xb") as destination:
                async for chunk in response.aiter_bytes():
                    self._raise_if_cancelled(cancel_requested, prompt_id)
                    total += len(chunk)
                    if total > self.max_download_bytes:
                        raise ValueError("ComfyUI video exceeds the configured output limit")
                    destination.write(chunk)
        if total == 0:
            raise ValueError("ComfyUI returned an empty video")

    async def _interrupt(self, client: httpx.AsyncClient, prompt_id: str | None) -> None:
        if prompt_id is not None:
            response = await client.post("/queue", json={"delete": [prompt_id]})
            if response.status_code not in {200, 404}:
                self._raise_http(response, "delete_queued_prompt", prompt_id)
        response = await client.post("/interrupt", json={})
        if response.status_code not in {200, 204}:
            self._raise_http(response, "interrupt", prompt_id)

    async def _free_models(self, client: httpx.AsyncClient) -> bool:
        try:
            response = await client.post("/free", json={"unload_models": True, "free_memory": True})
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    async def _json_get(self, client: httpx.AsyncClient, path: str) -> dict[str, Any]:
        response = await client.get(path)
        self._raise_http(response, "health_or_poll")
        return _json_object(response)

    async def _json_post(
        self, client: httpx.AsyncClient, path: str, payload: dict[str, object]
    ) -> dict[str, Any]:
        response = await client.post(path, json=payload)
        self._raise_http(response, "submit")
        return _json_object(response)

    def _raise_http(
        self, response: httpx.Response, operation: str, prompt_id: str | None = None
    ) -> None:
        if response.status_code < 400:
            return
        kind = (
            ProviderFailureKind.INVALID_INPUT
            if response.status_code in {400, 409, 422}
            else ProviderFailureKind.EXECUTION_FAILED
        )
        raise ProviderExecutionError(
            provider_id=self.provider_id,
            operation=operation,
            failure_kind=kind,
            retryable=response.status_code >= 500,
            backend_code=f"http_{response.status_code}",
            provider_job_id=prompt_id,
        )

    def _raise_if_cancelled(
        self, cancel_requested: CancelCheck | None, prompt_id: str | None
    ) -> None:
        if cancel_requested is not None and cancel_requested():
            raise ProviderExecutionError(
                provider_id=self.provider_id,
                operation="generate_video",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="local_comfyui_cancelled",
                provider_job_id=prompt_id,
            )

    def _missing_models(self, records: dict[str, dict[str, Any]]) -> list[str]:
        missing: list[str] = []
        checks = (
            ("UNETLoader", "unet_name", (HIGH_MODEL, LOW_MODEL)),
            ("CLIPLoader", "clip_name", (TEXT_ENCODER,)),
            ("VAELoader", "vae_name", (VAE,)),
        )
        for node, field, expected in checks:
            node_record = records[node].get(node)
            required = (
                node_record.get("input", {}).get("required", {})
                if isinstance(node_record, dict)
                else {}
            )
            spec = required.get(field) if isinstance(required, dict) else None
            choices = spec[0] if isinstance(spec, list) and spec else []
            for filename in expected:
                if filename not in choices:
                    missing.append(filename)
        return missing


def _validate_loopback_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("ComfyUI endpoint must be a plain local HTTP origin")
    if parsed.hostname == "localhost":
        return
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise ValueError("ComfyUI endpoint must use a loopback address") from error
    if not address.is_loopback:
        raise ValueError("ComfyUI endpoint must use a loopback address")


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("ComfyUI returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("ComfyUI response must be a JSON object")
    return payload


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value or len(value) > 300:
        raise ValueError(f"ComfyUI response has invalid {field}")
    return value


def _collect_placeholders(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value.startswith("{{") and value.endswith("}}") else set()
    if isinstance(value, list):
        return set().union(*(_collect_placeholders(item) for item in value), set())
    if isinstance(value, dict):
        return set().union(*(_collect_placeholders(item) for item in value.values()), set())
    return set()


def _replace_placeholders(value: Any, replacements: dict[str, object]) -> Any:
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    return value


def _bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Wan setting {field} must be an integer in range")
    return value


def _bounded_float(value: object, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Wan setting {field} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"Wan setting {field} is outside its range")
    return result


def _history_video_output(record: dict[str, Any], output_node_id: str) -> dict[str, str] | None:
    outputs = record.get("outputs")
    output = outputs.get(output_node_id) if isinstance(outputs, dict) else None
    videos = output.get("images") if isinstance(output, dict) else None
    animated = output.get("animated") if isinstance(output, dict) else None
    if (
        not isinstance(videos, list)
        or len(videos) != 1
        or not isinstance(videos[0], dict)
        or animated != [True]
    ):
        return None
    candidate = videos[0]
    filename = candidate.get("filename")
    subfolder = candidate.get("subfolder", "")
    record_type = candidate.get("type")
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not filename.lower().endswith(".mp4")
        or not isinstance(subfolder, str)
        or Path(subfolder).is_absolute()
        or ".." in Path(subfolder).parts
        or record_type != "output"
    ):
        raise ValueError("ComfyUI history contains an unsafe video output location")
    return {"filename": filename, "subfolder": subfolder, "type": record_type}


def _structured_oom_code(record: dict[str, Any]) -> str | None:
    status = record.get("status")
    messages = status.get("messages") if isinstance(status, dict) else None
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, list) or len(message) != 2 or message[0] != "execution_error":
            continue
        detail = message[1]
        if not isinstance(detail, dict):
            continue
        exception_type = detail.get("exception_type")
        if exception_type in {"torch.OutOfMemoryError", "OutOfMemoryError"}:
            return "comfyui_torch_out_of_memory"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ComfyUIWanFirstLastFrameProvider",
    "HIGH_MODEL",
    "LOW_MODEL",
    "MODEL_IDENTITY",
    "TEXT_ENCODER",
    "VAE",
]
