"""Pydantic models.

Two groups:
  1) The strict structured-output schema the GPT model must return per page
     (PageExtraction + nested chart/series/axis models).
  2) API request models used by the REST editing endpoints.

The chart spec is deliberately renderer-agnostic (maps cleanly onto ECharts on
the frontend) and every field is present (Optional -> nullable) so it satisfies
OpenAI strict Structured Outputs.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ChartType = Literal["line", "bar", "scatter", "pie", "area"]
AxisScale = Literal["linear", "log"]
AnnotationPos = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


class Annotation(BaseModel):
    """An in-plot text box (e.g. a datasheet 'Note: 1.TA=25C 2.Pulse test')."""
    text: str = Field(..., description="Text of the note; may contain newlines for multiple lines.")
    position: AnnotationPos = "top-left"


# --------------------------------------------------------------------------- #
# Structured-output models (what gpt-5.6-sol returns)
# --------------------------------------------------------------------------- #
class Point(BaseModel):
    """One data point. Use x/y for numeric charts; `label` for categorical
    (bar/pie) categories. Estimated values are fine — flag via confidence."""
    x: Optional[float] = Field(None, description="Numeric x value (line/scatter/area/numeric-bar).")
    y: Optional[float] = Field(None, description="Numeric y value / magnitude.")
    label: Optional[str] = Field(None, description="Category label for bar/pie points.")


class Series(BaseModel):
    name: str = Field(..., description="Series/legend name, e.g. 'Tj = 150C'.")
    color: Optional[str] = Field(None, description="Hex color if readable from the chart, else null.")
    conditions: Optional[str] = Field(None, description="Extra condition text, e.g. 'VCE=-1V'.")
    points: list[Point] = Field(default_factory=list)


class Axis(BaseModel):
    label: Optional[str] = None
    unit: Optional[str] = None
    scale: AxisScale = "linear"
    min: Optional[float] = None
    max: Optional[float] = None


class Confidence(BaseModel):
    overall: float = 0.5
    axis: float = 0.5
    legend: float = 0.5
    data: float = 0.5


class ExtractedChart(BaseModel):
    """A single chart the model found on a page."""
    title: Optional[str] = None
    subtitle: Optional[str] = None
    chart_type: ChartType = "line"
    stacked: bool = False
    x_axis: Axis = Field(default_factory=Axis)
    y_axis: Axis = Field(default_factory=Axis)
    series: list[Series] = Field(default_factory=list)
    legend: bool = True
    # Datasheet-style rendering:
    inline_labels: bool = Field(True, description="True if each line is labelled directly on the plot (near its end) rather than via a legend box.")
    show_markers: bool = Field(False, description="True only if the original plot draws visible point markers on the curves.")
    annotations: list[Annotation] = Field(default_factory=list, description="In-plot text boxes such as note boxes.")
    notes: Optional[str] = Field(None, description="Anything relevant a human editor should know.")
    # Normalized bbox [x0, y0, x1, y1] in 0..1 relative to the page image, used
    # to crop the original chart region for side-by-side comparison.
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    confidence: Confidence = Field(default_factory=Confidence)


class PageExtraction(BaseModel):
    """Full structured result for one page image."""
    page_number: int
    summary: str = Field(..., description="Concise summary of the page content.")
    has_charts: bool = False
    charts: list[ExtractedChart] = Field(default_factory=list)
    # Rolling continuity notes (abbreviations, legend/color defs, entities) the
    # model should carry into the next page. Kept bounded.
    updated_running_notes: str = Field("", description="Compact notes to carry to the next page.")


# --------------------------------------------------------------------------- #
# REST request models (frontend editing)
# --------------------------------------------------------------------------- #
class ChartSpec(BaseModel):
    """The editable, renderable chart spec stored in each version.

    Superset of ExtractedChart plus identity fields the backend assigns.
    """
    title: Optional[str] = None
    subtitle: Optional[str] = None
    chart_type: ChartType = "line"
    stacked: bool = False
    x_axis: Axis = Field(default_factory=Axis)
    y_axis: Axis = Field(default_factory=Axis)
    series: list[Series] = Field(default_factory=list)
    legend: bool = True
    inline_labels: bool = True
    show_markers: bool = False
    annotations: list[Annotation] = Field(default_factory=list)
    notes: Optional[str] = None
    confidence: Confidence = Field(default_factory=Confidence)


class SaveVersionRequest(BaseModel):
    spec: ChartSpec
    label: Optional[str] = None
