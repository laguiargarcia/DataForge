from pydantic import BaseModel, model_validator, field_validator
from typing import Any, List, Literal, Optional
from enum import Enum

_CONDITIONS = {"succeeded", "failed", "completed", "skipped"}


class EngineType(str, Enum):
    duckdb = "duckdb"
    pyspark = "pyspark"


class DatabaseType(str, Enum):
    sqlite = "sqlite"
    postgresql = "postgresql"


class RetryConfig(BaseModel):
    attempts: int = 3
    delay_seconds: int = 30


class Connection(BaseModel):
    type: str
    path: Optional[str] = None


class DependencyEdge(BaseModel):
    task: str
    when: List[str] = ["succeeded"]

    @field_validator("when", mode="before")
    @classmethod
    def _normalize_when(cls, v):
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            raise ValueError(f"'when' must be a string or list, got {type(v).__name__}")
        expanded: list[str] = []
        for item in v:
            if item == "completed":
                expanded.extend(["succeeded", "failed"])
            elif item in _CONDITIONS:
                expanded.append(item)
            else:
                raise ValueError(
                    f"invalid 'when' value '{item}'; allowed: {sorted(_CONDITIONS)}"
                )
        seen: set[str] = set()
        out: list[str] = []
        for s in expanded:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out


class Task(BaseModel):
    name: str
    type: Optional[str] = None
    script: Optional[str] = None
    job: Optional[str] = None
    params: dict = {}
    depends_on: List[DependencyEdge] = []
    match: Literal["any", "all"] = "all"
    retry: RetryConfig = RetryConfig()
    active: bool = True

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_edges(cls, v):
        if v is None:
            return []
        out = []
        for entry in v:
            if isinstance(entry, str):
                out.append({"task": entry, "when": ["succeeded"]})
            elif isinstance(entry, dict):
                out.append(entry)
            elif isinstance(entry, DependencyEdge):
                out.append(entry)
            else:
                raise ValueError(
                    f"depends_on entry must be string or object, got {type(entry).__name__}"
                )
        return out

    @model_validator(mode="after")
    def _infer_type_and_validate(self):
        if self.type is None:
            if self.script and not self.job:
                self.type = "python"
            elif self.job and not self.script:
                self.type = "job"
            else:
                raise ValueError(
                    f"Task '{self.name}': must declare 'type:' or exactly one of "
                    "'script:' / 'job:' for backward-compat inference"
                )
        return self


class JobParameter(BaseModel):
    name: str
    type: Literal["string", "int", "float", "bool", "date", "json"] = "string"
    default: Optional[Any] = None
    description: Optional[str] = None


class Job(BaseModel):
    name: str
    tasks: List[Task] = []
    retry: RetryConfig = RetryConfig()
    env_file: Optional[str] = None
    schedule: Optional[str] = None
    parameters: List[JobParameter] = []


class Workspace(BaseModel):
    name: str
    version: str = "1.0"
    engine: EngineType = EngineType.duckdb
    database: DatabaseType = DatabaseType.sqlite


class TriggerJobEntry(BaseModel):
    workspace: str
    job: str
    params: dict = {}


class TriggerModel(BaseModel):
    name: str
    cron: str
    enabled: bool = True
    jobs: List[TriggerJobEntry] = []


