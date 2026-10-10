from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=360)]
Workplace = Literal["any", "remote", "hybrid", "onsite"]
EMPLOYMENT_TYPES = {"full-time", "part-time", "contract", "internship", "temporary"}


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    headline: Annotated[str, Field(max_length=160)]
    skills: Annotated[list[ShortText], Field(max_length=30)]
    experience: Annotated[list[EvidenceText], Field(max_length=15)]
    credentials: Annotated[list[ShortText], Field(max_length=10)]


class Dealbreaker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["location", "workplace", "min_salary", "company", "employment_type"]
    value: ShortText

    @model_validator(mode="after")
    def normalize_value(self):
        if self.kind == "workplace":
            self.value = self.value.lower().replace("on-site", "onsite")
            if self.value not in {"remote", "hybrid", "onsite"}:
                raise ValueError("expected remote, hybrid, or onsite")
        elif self.kind == "employment_type":
            self.value = self.value.lower().replace(" ", "-")
            if self.value not in EMPLOYMENT_TYPES:
                raise ValueError("unsupported employment type")
        elif self.kind == "min_salary":
            import re

            if not re.fullmatch(r"[A-Za-z]{3} [1-9][0-9]{0,8}", self.value):
                raise ValueError("expected annual salary floor as currency + integer, e.g. USD 120000")
            self.value = self.value.upper()
        return self


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_roles: Annotated[list[ShortText], Field(max_length=12)] = Field(default_factory=list)
    locations: Annotated[list[ShortText], Field(max_length=12)] = Field(default_factory=list)
    remote_preference: Workplace = "any"
    dealbreakers: Annotated[list[Dealbreaker], Field(max_length=20)] = Field(default_factory=list)

    @field_validator("target_roles", "locations")
    @classmethod
    def unique_text(cls, values: list[str]) -> list[str]:
        return list({value.casefold(): value for value in values}.values())

    @field_validator("dealbreakers")
    @classmethod
    def unique_rules(cls, values: list[Dealbreaker]) -> list[Dealbreaker]:
        return list({(rule.kind, rule.value.casefold()): rule for rule in values}.values())
