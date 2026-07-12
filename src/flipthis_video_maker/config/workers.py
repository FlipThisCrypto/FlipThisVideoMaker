from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    assignment: str = Field(min_length=1, max_length=20)
    physical_gpu: int | None = Field(default=None, ge=0)
    max_concurrent_jobs: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def gpu_assignment_has_a_physical_device(self) -> Self:
        if self.assignment.startswith("gpu") and self.physical_gpu is None:
            raise ValueError("GPU worker assignments require physical_gpu")
        return self


class WorkerConfigurationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    workers: list[WorkerConfiguration]

    @model_validator(mode="after")
    def worker_ids_are_unique(self) -> Self:
        worker_ids = [worker.id for worker in self.workers]
        if len(worker_ids) != len(set(worker_ids)):
            raise ValueError("Worker configuration IDs must be unique")
        physical_gpus = [
            worker.physical_gpu for worker in self.workers if worker.physical_gpu is not None
        ]
        if len(physical_gpus) != len(set(physical_gpus)):
            raise ValueError("Physical GPUs may be assigned to only one worker")
        return self

    def require(self, worker_id: str) -> WorkerConfiguration:
        worker = next((item for item in self.workers if item.id == worker_id), None)
        if worker is None:
            raise KeyError(f"Unknown worker configuration: {worker_id}")
        return worker


def load_worker_configuration(path: Path) -> WorkerConfigurationFile:
    if not path.is_file():
        raise FileNotFoundError(f"Worker configuration not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return WorkerConfigurationFile.model_validate(data)
