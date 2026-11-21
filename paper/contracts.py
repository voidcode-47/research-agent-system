"""Typed, bounded contracts at model, HTTP and backup trust boundaries."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Text = Annotated[str, StringConstraints(max_length=200_000)]
Short = Annotated[str, StringConstraints(max_length=500)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class Issue(Contract):
    issue: Short = ""
    title: Short = ""
    severity: Short = "medium"
    evidence: Text = ""
    suggestion: Text = ""
    basis: Text = ""
    page: int | None = Field(default=0, ge=0, le=1_000_000)
    paragraph_id: Short = ""


class Diagnosis(Contract):
    score: float = Field(ge=0, le=100)
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    issues: list[Issue] = Field(default_factory=list, max_length=100)
    risks: list[Short] = Field(default_factory=list, max_length=100)
    minimal_plan: Text = ""


class Paragraph(Contract):
    id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    text: Text
    type: Short | None = "body"
    page: int | None = Field(default=0, ge=0, le=1_000_000)
    level: int | None = Field(default=1, ge=0, le=100)
    section: Short = ""


class Section(Contract):
    id: Short = ""
    title: Short = ""
    level: int = Field(default=1, ge=0, le=100)
    paragraph_ids: list[Short] = Field(default_factory=list, max_length=100_000)


class Table(Contract):
    id: Short = ""
    paragraph_id: Short = ""
    caption: Text = ""
    rows: list[Annotated[list[Text], Field(max_length=1000)]] = Field(default_factory=list, max_length=10_000)
    header_rows: int = Field(default=0, ge=0, le=10_000)


class Parsed(Contract):
    paragraphs: list[Paragraph] = Field(max_length=100_000)
    sections: list[Section] = Field(default_factory=list, max_length=10_000)
    tables: list[Table] = Field(default_factory=list, max_length=5000)
    title: Short = ""
    abstract: Text = ""
    keywords: list[Short] = Field(default_factory=list, max_length=100)


class Version(Contract):
    paragraphs: list[Paragraph] = Field(max_length=100_000)
    version: int = Field(ge=0, le=1_000_000)
    note: Text = ""


class Format(Contract):
    template: Literal["generic_thesis", "journal_cn", "ieee", "apa"] = "generic_thesis"
    reference_style: Literal["gbt7714", "ieee", "apa"] = "gbt7714"
    # True 表示字段值来自需求表单，导出时不再按模板默认值二次覆盖
    explicit: bool = False
    font_body: Annotated[str, StringConstraints(min_length=1, max_length=100)] = "宋体"
    font_latin: Annotated[str, StringConstraints(min_length=1, max_length=100)] = "Times New Roman"
    font_size_body_pt: float = Field(default=12, ge=8, le=24)
    line_spacing: float = Field(default=1.5, ge=1, le=3)
    space_before_pt: float = Field(default=0, ge=0, le=72)
    space_after_pt: float = Field(default=0, ge=0, le=72)
    first_line_indent_chars: float = Field(default=2, ge=0, le=10)
    page_break_before_heading: bool = True
    heading_numbering: Literal["decimal", "chinese", "none"] = "decimal"
    figure_caption_below: bool = True
    table_caption_above: bool = True


class Deai(Contract):
    strength: Literal["light", "medium", "strong"] = "medium"
    keep: list[Short] = Field(default_factory=list, max_length=100)
    banned_phrases: list[Short] = Field(default_factory=list, max_length=100)


class Requirement(Contract):
    level: Short = ""
    format: Format = Field(default_factory=Format)
    deai: Deai = Field(default_factory=Deai)
    notes: Annotated[str, StringConstraints(max_length=10_000)] = ""
    confirmed: bool = False


class Rewrite(Contract):
    revised: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    reasons: list[Short] = Field(default_factory=list, max_length=100)


class Term(Contract):
    term: Short = ""
    preferred: Short = ""
    reason: Text = ""


class Pattern(Contract):
    pattern: Short = ""
    examples: list[Short] = Field(default_factory=list, max_length=100)
    advice: Text = ""


class Guide(Contract):
    summary: Text = ""
    terminology: list[Term] = Field(default_factory=list, max_length=100)
    repeated_patterns: list[Pattern] = Field(default_factory=list, max_length=100)
    transitions: list[Short] = Field(default_factory=list, max_length=100)
    style_rules: list[Short] = Field(default_factory=list, max_length=100)


def validate(data, schema, label="数据"):
    try:
        return schema.model_validate(data)
    except ValidationError as error:
        first = error.errors(include_input=False)[0]
        path = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"{label} 结构非法：{path} {first['msg']}") from None


def valid(data, schema) -> bool:
    try:
        validate(data, schema)
        return True
    except ValueError:
        return False
