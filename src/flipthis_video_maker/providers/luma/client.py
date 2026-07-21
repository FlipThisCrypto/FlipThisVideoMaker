import asyncio
import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from flipthis_video_maker.contracts.video_generation import (
    GenerationCategory,
    LipSyncMode,
    utc_now,
)
from flipthis_video_maker.media.ffmpeg import probe
from flipthis_video_maker.providers.base.errors import (
    ProviderExecutionError,
    ProviderFailureKind,
)
from flipthis_video_maker.providers.base.models import Capability, ProviderInfo
from flipthis_video_maker.providers.base.video_generation import (
    ProviderRunOutput,
    ResolvedFirstLastFrameRequest,
)

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class LumaRayVideoProvider:
    """Ray 3.2 adapter for the documented Luma Agents async generation API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoint: str = "https://agents.lumalabs.ai/v1",
        model: str = "ray-3.2",
        timeout_seconds: float = 900,
        poll_interval_seconds: float = 2,
        max_download_mb: int = 2048,
        max_inline_image_mb: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip() if api_key else None
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_download_bytes = max_download_mb * 1024 * 1024
        self.max_inline_image_bytes = max_inline_image_mb * 1024 * 1024
        self.transport = transport

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="luma-ray",
            name="Luma Ray 3.2 first/last-frame generation",
            model_identity=self.model,
            capabilities={
                Capability.VIDEO_GENERATION,
                Capability.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
            },
            available=self.api_key is not None,
            supported_inputs={"image/png", "image/jpeg", "prompt"},
            max_duration_seconds=10,
            max_width=1920,
            max_height=1080,
            native_frame_rates=set(),
            supported_durations_seconds={5, 10},
            generation_category=GenerationCategory.FIRST_LAST_FRAME_GENERATIVE_VIDEO,
            cancellation_supported=False,
            progress_supported=False,
            notes=(
                "True Ray 3.2 keyframe-conditioned generation. Keyframe positions use a 24-fps "
                "grid; the output's actual native FPS is measured rather than assumed. The "
                "provider exposes no cancellation endpoint or percentage progress."
            ),
        )

    async def health(self) -> dict[str, object]:
        if self.api_key is None:
            return {"ok": False, "status": "missing_credentials"}
        try:
            async with self._client() as client:
                response = await client.get("/generations/00000000-0000-0000-0000-000000000000")
        except httpx.HTTPError as error:
            return {
                "ok": False,
                "status": "unreachable",
                "error_type": type(error).__name__,
            }
        if response.status_code in {200, 404}:
            return {
                "ok": True,
                "status": "authenticated",
                "api_version": response.headers.get("X-API-Version"),
            }
        return {
            "ok": False,
            "status": "authentication_failed"
            if response.status_code in {401, 403}
            else "unexpected_response",
            "http_status": response.status_code,
        }

    async def generate(
        self,
        request: ResolvedFirstLastFrameRequest,
        *,
        cancel_requested: CancelCheck | None = None,
        progress: ProgressCallback | None = None,
    ) -> ProviderRunOutput:
        self._validate_request(request)
        if self.api_key is None:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="generate_video",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
                backend_code="missing_credentials",
            )
        self._raise_if_cancelled(cancel_requested)
        payload = self._payload(request)
        submitted_at = utc_now()
        started = time.monotonic()
        provider_job_id: str | None = None
        api_version: str | None = None
        try:
            async with self._client() as client:
                response = await client.post("/generations", json=payload)
                api_version = response.headers.get("X-API-Version")
                self._raise_for_http(response, operation="submit")
                body = self._response_json(response, operation="submit")
                provider_job_id = self._required_string(body, "id", operation="submit")
                if progress:
                    progress(0.1, "provider_queued")
                terminal = await self._poll(
                    client,
                    provider_job_id,
                    cancel_requested=cancel_requested,
                    progress=progress,
                )
                provider_seconds = time.monotonic() - started
                output_url = self._video_output_url(terminal, provider_job_id)
                if progress:
                    progress(0.8, "downloading_native_video")
                download_started = time.monotonic()
                await self._download(
                    output_url,
                    request.output_path,
                    cancel_requested=cancel_requested,
                    provider_job_id=provider_job_id,
                )
                download_seconds = time.monotonic() - download_started
        except ProviderExecutionError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="generate_video",
                failure_kind=ProviderFailureKind.TIMEOUT,
                retryable=True,
                backend_code="client_timeout",
                provider_job_id=provider_job_id,
            ) from error
        except httpx.HTTPError as error:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="generate_video",
                retryable=True,
                backend_code=type(error).__name__,
                provider_job_id=provider_job_id,
            ) from error

        self._validate_output(request.output_path, provider_job_id)
        if progress:
            progress(0.9, "native_video_validated")
        return ProviderRunOutput(
            output_path=request.output_path,
            provider_job_id=provider_job_id,
            actual_model=self._optional_string(terminal, "model") or self.model,
            api_version=api_version,
            submitted_at=submitted_at,
            completed_at=utc_now(),
            provider_seconds=provider_seconds,
            download_seconds=download_seconds,
            actual_settings={
                "duration": f"{request.snapshot.duration_seconds:g}s",
                "resolution": f"{request.snapshot.height}p",
                "aspect_ratio": request.snapshot.aspect_ratio,
                "keyframe_indexes": [0, round(request.snapshot.duration_seconds * 24)],
            },
            warnings=("Provider does not support server-side cancellation",),
        )

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(
            base_url=self.endpoint,
            headers=headers,
            timeout=httpx.Timeout(30, read=60),
            transport=self.transport,
            follow_redirects=False,
        )

    def _validate_request(self, request: ResolvedFirstLastFrameRequest) -> None:
        snapshot = request.snapshot
        if request.output_path.exists():
            raise ValueError("Luma output destination already exists")
        if snapshot.provider_id != "luma-ray" or snapshot.provider_model != self.model:
            raise ValueError("Request provider identity does not match the Luma adapter")
        if snapshot.duration_seconds not in {5, 10}:
            raise ValueError("Ray 3.2 supports exactly 5- or 10-second video generation")
        if snapshot.native_requested_fps != 24:
            raise ValueError("Ray 3.2 keyframe positions use a documented 24 FPS grid")
        if snapshot.height not in {360, 540, 720, 1080}:
            raise ValueError("Ray 3.2 resolution must be 360p, 540p, 720p, or 1080p")
        if snapshot.aspect_ratio not in {"16:9", "4:3", "3:2", "1:1", "3:4", "2:3", "9:16"}:
            raise ValueError("Unsupported Ray 3.2 video aspect ratio")
        if snapshot.negative_prompt:
            raise ValueError("Ray 3.2 Agents video generation does not document negative_prompt")
        if snapshot.seed is not None:
            raise ValueError("Ray 3.2 Agents video generation does not document a seed parameter")
        if snapshot.motion_strength is not None:
            raise ValueError("Ray 3.2 does not expose motion strength for type=video")
        if snapshot.identity_reference_asset_ids:
            raise ValueError("Ray 3.2 FLF generation does not expose identity references")
        if (
            snapshot.audio_reference_asset_id is not None
            and snapshot.lip_sync_mode is not LipSyncMode.LATENTSYNC
        ):
            raise ValueError("Ray 3.2 FLF generation does not accept audio conditioning")
        if snapshot.provider_settings.get("luma-ray", {}):
            raise ValueError("No provider-specific Ray 3.2 settings are currently supported")
        for path, mime_type in (
            (request.start_frame_path, request.start_frame_mime_type),
            (request.end_frame_path, request.end_frame_mime_type),
        ):
            if mime_type not in {"image/png", "image/jpeg"}:
                raise ValueError("Ray 3.2 keyframes must be PNG or JPEG Assets")
            if not path.is_file():
                raise ValueError("Resolved keyframe Asset file is missing")
            if path.stat().st_size > self.max_inline_image_bytes:
                raise ValueError("Keyframe exceeds the configured inline upload limit")

    def _payload(self, request: ResolvedFirstLastFrameRequest) -> dict[str, object]:
        snapshot = request.snapshot
        return {
            "model": self.model,
            "type": "video",
            "prompt": self._combined_prompt(snapshot.prompt, snapshot.camera_direction),
            "aspect_ratio": snapshot.aspect_ratio,
            "video": {
                "duration": f"{snapshot.duration_seconds:g}s",
                "resolution": f"{snapshot.height}p",
                "keyframes": [
                    self._image_ref(request.start_frame_path, request.start_frame_mime_type),
                    self._image_ref(request.end_frame_path, request.end_frame_mime_type),
                ],
                "keyframe_indexes": [0, round(snapshot.duration_seconds * 24)],
            },
        }

    @staticmethod
    def _combined_prompt(prompt: str, camera_direction: str) -> str:
        if camera_direction == "natural":
            return prompt
        return f"{prompt}\nCamera direction: {camera_direction}"

    @staticmethod
    def _image_ref(path: Path, mime_type: str) -> dict[str, str]:
        return {
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "media_type": mime_type,
        }

    async def _poll(
        self,
        client: httpx.AsyncClient,
        provider_job_id: str,
        *,
        cancel_requested: CancelCheck | None,
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            self._raise_if_cancelled(cancel_requested, provider_job_id)
            if time.monotonic() >= deadline:
                raise ProviderExecutionError(
                    provider_id="luma-ray",
                    operation="poll",
                    failure_kind=ProviderFailureKind.TIMEOUT,
                    retryable=True,
                    backend_code="poll_deadline",
                    provider_job_id=provider_job_id,
                )
            response = await client.get(f"/generations/{provider_job_id}")
            self._raise_for_http(
                response,
                operation="poll",
                provider_job_id=provider_job_id,
            )
            body = self._response_json(response, operation="poll")
            state = self._required_string(body, "state", operation="poll")
            if state == "completed":
                return body
            if state == "failed":
                self._raise_provider_failure(body, provider_job_id)
            if state not in {"queued", "processing"}:
                raise ProviderExecutionError(
                    provider_id="luma-ray",
                    operation="poll",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="unknown_state",
                    provider_job_id=provider_job_id,
                )
            if progress:
                progress(0.2 if state == "queued" else 0.5, f"provider_{state}")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _download(
        self,
        url: str,
        destination: Path,
        *,
        cancel_requested: CancelCheck | None,
        provider_job_id: str,
    ) -> None:
        parsed_url = httpx.URL(url)
        if parsed_url.scheme != "https" or not parsed_url.host:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="download",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="unsafe_output_url",
                provider_job_id=provider_job_id,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f".{destination.name}.{provider_job_id}.partial")
        size = 0
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30, read=300),
                transport=self.transport,
                follow_redirects=False,
            ) as download_client:
                async with download_client.stream("GET", url) as response:
                    self._raise_for_http(
                        response,
                        operation="download",
                        provider_job_id=provider_job_id,
                    )
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    if content_type and content_type not in {
                        "video/mp4",
                        "application/octet-stream",
                    }:
                        raise ProviderExecutionError(
                            provider_id="luma-ray",
                            operation="download",
                            failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                            backend_code="unexpected_content_type",
                            provider_job_id=provider_job_id,
                        )
                    with partial.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            self._raise_if_cancelled(cancel_requested, provider_job_id)
                            size += len(chunk)
                            if size > self.max_download_bytes:
                                raise ProviderExecutionError(
                                    provider_id="luma-ray",
                                    operation="download",
                                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                                    backend_code="output_too_large",
                                    provider_job_id=provider_job_id,
                                )
                            handle.write(chunk)
            if size == 0:
                raise ProviderExecutionError(
                    provider_id="luma-ray",
                    operation="download",
                    failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                    backend_code="empty_output",
                    provider_job_id=provider_job_id,
                )
            partial.replace(destination)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_output(path: Path, provider_job_id: str) -> None:
        try:
            info = probe(path)
        except Exception as error:
            path.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="validate_output",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="ffprobe_failed",
                provider_job_id=provider_job_id,
            ) from error
        if not any(stream.get("codec_type") == "video" for stream in info.get("streams", [])):
            path.unlink(missing_ok=True)
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="validate_output",
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="missing_video_stream",
                provider_job_id=provider_job_id,
            )

    @staticmethod
    def _response_json(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_json",
            ) from error
        if not isinstance(body, dict):
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code="invalid_response_shape",
            )
        return body

    @staticmethod
    def _required_string(body: dict[str, Any], key: str, *, operation: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation=operation,
                failure_kind=ProviderFailureKind.OUTPUT_INVALID,
                backend_code=f"missing_{key}",
            )
        return value

    @staticmethod
    def _optional_string(body: dict[str, Any], key: str) -> str | None:
        value = body.get(key)
        return value if isinstance(value, str) and value else None

    def _video_output_url(self, body: dict[str, Any], provider_job_id: str) -> str:
        outputs = body.get("output")
        if isinstance(outputs, list):
            for output in outputs:
                if (
                    isinstance(output, dict)
                    and output.get("type") == "video"
                    and isinstance(output.get("url"), str)
                ):
                    return str(output["url"])
        raise ProviderExecutionError(
            provider_id="luma-ray",
            operation="collect_output",
            failure_kind=ProviderFailureKind.OUTPUT_INVALID,
            backend_code="missing_video_output",
            provider_job_id=provider_job_id,
        )

    def _raise_for_http(
        self,
        response: httpx.Response,
        *,
        operation: str,
        provider_job_id: str | None = None,
    ) -> None:
        if response.status_code < 400:
            return
        retry_after: float | None = None
        try:
            retry_after = float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            pass
        mapping = {
            401: (ProviderFailureKind.AUTHENTICATION, False, "http_401"),
            402: (ProviderFailureKind.BUDGET_EXHAUSTED, False, "http_402"),
            403: (ProviderFailureKind.AUTHENTICATION, False, "http_403"),
            413: (ProviderFailureKind.INVALID_INPUT, False, "http_413"),
            422: (ProviderFailureKind.INVALID_INPUT, False, "http_422"),
            429: (ProviderFailureKind.RATE_LIMITED, True, "http_429"),
        }
        kind, retryable, code = mapping.get(
            response.status_code,
            (
                ProviderFailureKind.EXECUTION_FAILED,
                response.status_code >= 500,
                f"http_{response.status_code}",
            ),
        )
        raise ProviderExecutionError(
            provider_id="luma-ray",
            operation=operation,
            failure_kind=kind,
            retryable=retryable,
            backend_code=code,
            provider_job_id=provider_job_id,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _raise_provider_failure(body: dict[str, Any], provider_job_id: str) -> None:
        code = body.get("failure_code")
        backend_code = code if isinstance(code, str) else "generation_failed"
        mapping = {
            "content_moderated": ProviderFailureKind.CONTENT_MODERATED,
            "budget_exhausted": ProviderFailureKind.BUDGET_EXHAUSTED,
            "rate_limited": ProviderFailureKind.RATE_LIMITED,
            "image_too_large": ProviderFailureKind.INVALID_INPUT,
            "unsupported_format": ProviderFailureKind.INVALID_INPUT,
            "corrupt_input": ProviderFailureKind.INVALID_INPUT,
            "invalid_request": ProviderFailureKind.INVALID_INPUT,
            "output_not_found": ProviderFailureKind.OUTPUT_INVALID,
        }
        kind = mapping.get(backend_code, ProviderFailureKind.EXECUTION_FAILED)
        raise ProviderExecutionError(
            provider_id="luma-ray",
            operation="generate_video",
            failure_kind=kind,
            retryable=kind
            in {
                ProviderFailureKind.RATE_LIMITED,
                ProviderFailureKind.EXECUTION_FAILED,
            },
            backend_code=backend_code,
            provider_job_id=provider_job_id,
        )

    @staticmethod
    def _raise_if_cancelled(
        cancel_requested: CancelCheck | None,
        provider_job_id: str | None = None,
    ) -> None:
        if cancel_requested is not None and cancel_requested():
            raise ProviderExecutionError(
                provider_id="luma-ray",
                operation="generate_video",
                failure_kind=ProviderFailureKind.CANCELLED,
                backend_code="local_polling_cancelled",
                provider_job_id=provider_job_id,
            )


__all__ = [
    "LumaRayVideoProvider",
    "ProviderRunOutput",
    "ResolvedFirstLastFrameRequest",
]
