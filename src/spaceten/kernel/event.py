from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


def utc_z(instant: datetime) -> str:
    """ISO-8601 UTC with a Z suffix; never +00:00."""
    utc = instant.astimezone(UTC)
    naive = utc.replace(tzinfo=None)
    if naive.microsecond:
        return naive.isoformat(timespec="microseconds") + "Z"
    return naive.isoformat(timespec="seconds") + "Z"


class _OpModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Init(_OpModel):
    op: Literal["init"] = "init"
    energy_cap_mj: int

    @field_validator("energy_cap_mj")
    @classmethod
    def _cap_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("energy_cap_mj must be non-negative")
        return value


class Observe(_OpModel):
    op: Literal["observe"] = "observe"
    address: str
    size_bytes: int
    content_hash: str | None
    listing: bool = False


class Plan(_OpModel):
    op: Literal["plan"] = "plan"
    provider: str
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    summary: str


class Act(_OpModel):
    op: Literal["act"] = "act"
    tool: str
    address: str
    digest: str
    size_bytes: int


class Finish(_OpModel):
    op: Literal["finish"] = "finish"
    summary: str
    artifact: str | None = None


class Failed(_OpModel):
    op: Literal["failed"] = "failed"
    code: str
    message: str
    attempted_mj: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0


Op = Annotated[Init | Observe | Plan | Act | Finish | Failed, Field(discriminator="op")]


class Event(BaseModel):
    # "schema" aliases the JSON key; BaseModel.schema would shadow a field of that name.
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_version: Literal[1] = Field(default=1, alias="schema")
    id: str
    seq: int
    wall: datetime
    parent: str | None
    actor: str
    energy_delta_mj: int
    energy_reason: Literal["io", "plan", "none"]
    op: Op

    @field_validator("wall", mode="before")
    @classmethod
    def _parse_wall(cls, value: object) -> object:
        if isinstance(value, str) and value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00")
        return value

    @field_serializer("wall", when_used="json")
    def _dump_wall(self, value: datetime) -> str:
        return utc_z(value)

    @model_validator(mode="after")
    def _energy_contract(self) -> Self:
        if self.energy_delta_mj > 0:
            raise ValueError("energy_delta_mj must be <= 0")
        if (self.energy_delta_mj == 0) != (self.energy_reason == "none"):
            raise ValueError("energy_delta_mj is 0 iff energy_reason == 'none'")
        return self
