"""Best-effort semantic observations from a streaming structured JSON response.

The final Pydantic object remains authoritative. This observer only emits values
that are already complete JSON tokens, allowing the UI to draw an explicitly
provisional chart while the response is still arriving.
"""
from __future__ import annotations

import json
import re
from typing import Any


_STRING = r'"(?:\\.|[^"\\])*"'
_CHART_TYPE_RE = re.compile(r'"chart_type"\s*:\s*("(?:line|bar|scatter|pie|area)")')
_POINTS_RE = re.compile(r'"points"\s*:\s*\[')
_POINT_RE = re.compile(r'\{[^{}]*\}')


def _last_json_string(text: str, key: str) -> str | None:
    matches = list(re.finditer(rf'"{re.escape(key)}"\s*:\s*({_STRING}|null)', text))
    if not matches:
        return None
    try:
        return json.loads(matches[-1].group(1))
    except (TypeError, ValueError):
        return None


def _object_after(text: str, key: str) -> dict[str, Any] | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', text)
    if not match:
        return None
    start = match.end() - 1
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : i + 1])
                    return value if isinstance(value, dict) else None
                except ValueError:
                    return None
    return None


class StructuredStreamObserver:
    """Turns the accumulated JSON snapshot into de-duplicated domain events."""

    def __init__(self, page_number: int) -> None:
        self.page_number = page_number
        self._charts_seen: set[int] = set()
        self._axes_seen: set[int] = set()
        self._series_seen: set[tuple[int, int]] = set()
        self._point_counts: dict[tuple[int, int], int] = {}
        self._summary: str | None = None

    def observe(self, snapshot: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        charts = list(_CHART_TYPE_RE.finditer(snapshot))

        summary_prefix = snapshot[: charts[0].start()] if charts else snapshot
        summary = _last_json_string(summary_prefix, "summary")
        if summary is not None and summary != self._summary:
            self._summary = summary
            out.append({"type": "page_summary_draft", "page_number": self.page_number, "summary": summary})

        for ci, chart_match in enumerate(charts):
            segment_end = charts[ci + 1].start() if ci + 1 < len(charts) else len(snapshot)
            segment = snapshot[chart_match.end() : segment_end]
            prior_start = charts[ci - 1].end() if ci else 0
            prefix = snapshot[prior_start : chart_match.start()]
            chart_type = json.loads(chart_match.group(1))
            title = _last_json_string(prefix, "title")
            draft_id = f"p{self.page_number:03d}_draft{ci + 1:02d}"

            if ci not in self._charts_seen:
                self._charts_seen.add(ci)
                out.append({
                    "type": "chart_draft_started", "page_number": self.page_number,
                    "chart_index": ci, "draft_id": draft_id, "title": title, "chart_type": chart_type,
                })

            if ci not in self._axes_seen:
                x_axis = _object_after(segment, "x_axis")
                y_axis = _object_after(segment, "y_axis")
                if x_axis is not None and y_axis is not None:
                    self._axes_seen.add(ci)
                    out.append({
                        "type": "chart_draft_axes", "page_number": self.page_number,
                        "chart_index": ci, "draft_id": draft_id, "x_axis": x_axis, "y_axis": y_axis,
                    })

            point_arrays = list(_POINTS_RE.finditer(segment))
            for si, points_match in enumerate(point_arrays):
                next_start = point_arrays[si + 1].start() if si + 1 < len(point_arrays) else len(segment)
                points_text = segment[points_match.end() : next_start]
                name_prefix_start = point_arrays[si - 1].end() if si else 0
                name_prefix = segment[name_prefix_start : points_match.start()]
                name = _last_json_string(name_prefix, "name") or f"Series {si + 1}"
                key = (ci, si)
                if key not in self._series_seen:
                    self._series_seen.add(key)
                    out.append({
                        "type": "series_draft_started", "page_number": self.page_number,
                        "chart_index": ci, "series_index": si, "draft_id": draft_id, "name": name,
                    })

                parsed_points: list[dict[str, Any]] = []
                for point_match in _POINT_RE.finditer(points_text):
                    try:
                        point = json.loads(point_match.group(0))
                    except ValueError:
                        continue
                    if isinstance(point, dict) and any(k in point for k in ("x", "y", "label")):
                        parsed_points.append(point)
                seen = self._point_counts.get(key, 0)
                if len(parsed_points) > seen:
                    self._point_counts[key] = len(parsed_points)
                    out.append({
                        "type": "points_draft_batch", "page_number": self.page_number,
                        "chart_index": ci, "series_index": si, "draft_id": draft_id,
                        "points": parsed_points[seen:], "point_count": len(parsed_points),
                    })
        return out
