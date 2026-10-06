"""Business-authored configuration. Cloud endpoints and credentials are platform-owned."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationCase(Strict):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    input: str = Field(min_length=1, max_length=4000)
    expected_response: str | None = Field(default=None, max_length=4000)
    assertions: list[str] = Field(default_factory=list, max_length=10)
    # Existing JSON uploads remain readable; these are separate structural checks.
    required_terms: list[str] = Field(default_factory=list, max_length=20)
    require_citation: bool = False
    expect_refusal: bool = False
    expected_format: Literal["text", "json"] = "text"

    @model_validator(mode="after")
    def reference_requirements(self):
        if not self.input.strip():
            raise ValueError("Evaluation input cannot be blank")
        if self.expect_refusal and not (self.expected_response or "").strip():
            raise ValueError("Provide an expected_response describing the expected refusal")
        return self

    @field_validator("assertions", "required_terms")
    @classmethod
    def bounded_strings(cls, values):
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("Use nonempty requirements of at most 1,000 characters")
        return values


class AgentDefinition(Strict):
    template_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=10, max_length=8000)
    mcp_servers: list[str] = Field(default_factory=list, max_length=4)
    tools: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=6)
    component_versions: dict[str, str]
    output_format: Literal["text", "json"] = "text"
    dataset: list[EvaluationCase] = Field(default_factory=list, max_length=20)
    minimum_score: float = Field(default=0.7, ge=0, le=1)

    @field_validator("dataset", mode="before")
    @classmethod
    def optional_dataset(cls, value):
        return [] if value is None else value

    @model_validator(mode="after")
    def unique_and_bounded(self):
        from foundation_harness.config import canonical
        if not self.name.strip() or not self.prompt.strip():
            raise ValueError("Provide an agent name and instructions")
        if any(len(set(values)) != len(values) for values in (self.mcp_servers, self.tools, self.skills)):
            raise ValueError("Select each MCP server, tool and skill once")
        if len({case.id for case in self.dataset}) != len(self.dataset):
            raise ValueError("Evaluation case IDs must be unique")
        if len(canonical([case.model_dump(exclude_none=True) for case in self.dataset])) > 32768:
            raise ValueError("Evaluation dataset must be at most 32 KiB")
        return self


class SaveAgent(Strict):
    definition: AgentDefinition
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    deploy: bool = True
    base_version: int | None = Field(default=None, ge=1)


class VersionAction(Strict):
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class InvokeAgent(VersionAction):
    input: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ResumeInvocation(Strict):
    version: int = Field(ge=1)
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class DeletePreview(Strict):
    version: int = Field(ge=1)


class DeleteAgent(VersionAction):
    confirmation_token: str = Field(min_length=40, max_length=100)
    confirm_name: str = Field(min_length=1, max_length=100)
