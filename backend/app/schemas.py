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

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ChartType = Literal["line", "bar", "scatter", "pie", "area"]
AxisScale = Literal["linear", "log"]
AnnotationPos = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


class Annotation(BaseModel):
    """An in-plot text box (e.g. a datasheet 'Note: 1.TA=25C 2.Pulse test')."""
    text: str = Field(..., description="Text of the note; may contain newlines for multiple lines.")
    position: AnnotationPos = "top-left"


# --------------------------------------------------------------------------- #
# Structured-output models (what the vision model returns)
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
    # Where this curve's label is printed on the plot (data coordinates of the label centre).
    label_x: Optional[float] = Field(None, description="X (data coords) of the on-plot label centre; null if a legend is used.")
    label_y: Optional[float] = Field(None, description="Y (data coords) of the on-plot label centre; null if a legend is used.")
    label_boxed: bool = Field(False, description="True if the on-plot label sits inside a bordered box.")
    # Stroke weight of the curve relative to the chart's own axis/grid lines.
    line_width: Optional[Literal["thin", "medium", "thick"]] = Field(
        None, description="thin ≈ as fine as the axis lines; medium ≈ twice that (typical); thick ≈ bold, 3× or more."
    )


class Axis(BaseModel):
    label: Optional[str] = None
    unit: Optional[str] = None
    scale: AxisScale = "linear"
    min: Optional[float] = None
    max: Optional[float] = None
    # Direction: True when the values DECREASE going up (y) / going right (x), e.g. a y-axis that
    # reads 0 at the bottom and -1.0 at the top (PNP / negative quantities). min/max stay numeric.
    inverse: bool = Field(False, description="True if axis values decrease along the axis direction (up for y, right for x).")
    # Tick/grid spacing read off the original (linear axes only; null on log axes).
    major_interval: Optional[float] = Field(None, description="Step between labelled ticks, e.g. 0,2,4,… -> 2.")
    minor_interval: Optional[float] = Field(None, description="Step between the finer unlabelled gridlines if present, else null.")


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
    smooth: bool = Field(True, description="True for smooth characteristic curves; false for piecewise-linear plots made of straight segments (e.g. SOA limits).")
    annotations: list[Annotation] = Field(default_factory=list, description="In-plot text boxes such as note boxes.")
    notes: Optional[str] = Field(None, description="Anything relevant a human editor should know.")
    # Normalized bbox [x0, y0, x1, y1] in 0..1 relative to the page image, used
    # to crop the original chart region for side-by-side comparison.
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
    # Normalized rectangle of the PLOT AREA itself (the region bounded by the axes,
    # excluding tick labels and titles), in the SAME frame as bbox. Lets the UI overlay
    # our curves precisely on the original crop.
    plot_bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0, 1.0])
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
    smooth: bool = True
    annotations: list[Annotation] = Field(default_factory=list)
    # Plot-area rectangle [x0,y0,x1,y1] normalized within the chart CROP image (for overlay).
    plot_rect: Optional[list[float]] = None
    # Stroke weight / text size measured from the crop (fractions of crop height); derived
    # data the renderer uses so the reconstruction matches the original's weight at any size.
    style_metrics: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    confidence: Confidence = Field(default_factory=Confidence)


class SaveVersionRequest(BaseModel):
    spec: ChartSpec
    label: Optional[str] = None


class RerunRequest(BaseModel):
    """Re-extract one chart, optionally on a different model / reasoning effort."""
    effort: Optional[str] = Field(None, description="none|low|medium|high|xhigh|max (defaults to server setting).")
    model: Optional[str] = Field(None, description="Model id override (defaults to server setting).")
