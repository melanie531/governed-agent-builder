from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Case(Strict):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    input: str = Field(min_length=1, max_length=2000)
    required_terms: list[str] = Field(default_factory=list, max_length=20)
    require_citation: bool = False
    expect_refusal: bool = False
    expected_format: Literal["text", "json"] = "text"

    @field_validator("required_terms")
    @classmethod
    def bounded_terms(cls, value):
        if any(not x.strip() or len(x) > 100 for x in value):
            raise ValueError("Required terms must be nonempty and at most 100 characters")
        return value

class Rubric(Strict):
    profile: Literal["local-deterministic", "llm-required"] = "local-deterministic"
    criteria: str = Field(default="Answers are accurate, concise, and grounded in evidence.", max_length=4000)
    minimum_score: float = Field(default=1, ge=0, le=1)

class DefinitionInput(Strict):
    name: str = Field(min_length=1, max_length=100)
    foundation_id: str
    foundation_version: str
    model_id: str
    component_versions: dict[str, str]
    tools: list[str] = Field(default_factory=list, max_length=4)
    skills: list[str] = Field(default_factory=list, max_length=4)
    prompt: str = Field(min_length=10, max_length=8000)
    output_format: Literal["text", "json"] = "text"
    dataset: list[Case] = Field(min_length=1, max_length=20)
    rubric: Rubric
    source: Literal["synthetic-local-only"] = "synthetic-local-only"
    base_version: int | None = None

    @field_validator("dataset")
    @classmethod
    def unique_ids(cls, cases):
        if len({c.id for c in cases}) != len(cases):
            raise ValueError("Case IDs must be unique")
        return cases

class Login(Strict):
    persona_id: Literal["alex", "sam", "admin"]

class Deploy(Strict):
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")

class Invoke(Strict):
    version: int
    input: str = Field(min_length=1, max_length=2000)

class CapabilityRequest(Strict):
    component_id: str
    reason: str = Field(min_length=5, max_length=1000)

class Decision(Strict):
    approve: bool
    reason: str = Field(min_length=5, max_length=1000)

class Grant(Strict):
    persona_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    component_id: str
    enabled: bool

class CatalogUpdate(Strict):
    approved: bool

class PolicyUpdate(Strict):
    require_judge: bool
    minimum_score: float = Field(ge=0, le=1)
