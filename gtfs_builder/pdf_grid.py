"""Low-level grid extraction shared by all SLE timetable tables.

These schedules draw each timetable as a true ruled grid: both vertical AND
horizontal rule lines are present (confirmed by inspecting `page.lines` on
the source PDFs -- every row boundary, including the header row's, has its
own horizontal line). So both axes come from vector geometry, not from
clustering word positions:

- column boundaries: x-positions of near-vertical lines
- row boundaries: y-positions of near-horizontal lines

Each table's header prints 2-3 sub-rows of text (a colored service-type
label like "SLE"/"SLE THRU"/"MNR", the train number, and sometimes a note
like "MON-THURS") stacked inside a single ruled row band -- so "one grid
row" doesn't always mean "one line of text", which is why we grab all words
whose vertical position falls anywhere inside a row's [y0, y1) band rather
than assuming one text baseline per row.
"""

from bisect import bisect_right
from dataclasses import dataclass


def _dedup(values: list[float], merge_tol: float) -> list[float]:
    merged = []
    for v in sorted(values):
        if merged and v - merged[-1] < merge_tol:
            continue
        merged.append(v)
    return merged


def column_boundaries(page, x_min, x_max, y_min, y_max, merge_tol=2.0) -> list[float]:
    """X-positions of vertical rule lines inside the given region, deduped."""
    xs = {
        round(line["x0"], 1)
        for line in page.lines
        if x_min <= line["x0"] <= x_max
        and y_min <= line["top"] <= y_max
        and abs(line["x0"] - line["x1"]) < 1  # vertical line
    }
    return _dedup(list(xs), merge_tol)


def row_boundaries(page, x_min, x_max, y_min, y_max, merge_tol=2.0) -> list[float]:
    """Y-positions of horizontal rule lines inside the given region, deduped."""
    ys = {
        round(line["top"], 1)
        for line in page.lines
        if x_min <= line["x0"] <= x_max
        and y_min <= line["top"] <= y_max
        and abs(line["top"] - line["bottom"]) < 1  # horizontal line
    }
    return _dedup(list(ys), merge_tol)


def slot_index(boundaries: list[float], value: float) -> int:
    """Which grid slot (0 = before boundaries[1]) does a position fall into?
    `boundaries` must include the region's outer edges, as returned by
    `column_boundaries`/`row_boundaries`. Used for both x0->column and
    top->row lookups (e.g. to place an embedded track-badge image)."""
    return bisect_right(boundaries, value) - 1


def cell_text(page, x0: float, x1: float, y0: float, y1: float, x_tolerance=1) -> str:
    """All word text inside the half-open box [x0,x1) x [y0,y1), left to
    right, top to bottom (so a 2-3-line header cell comes out in reading
    order, e.g. "SLE THRU 1633")."""
    words = [
        w
        for w in page.extract_words(x_tolerance=x_tolerance)
        if x0 <= w["x0"] < x1 and y0 <= w["top"] < y1
    ]
    words.sort(key=lambda w: (w["top"], w["x0"]))
    return " ".join(w["text"] for w in words)


@dataclass
class Grid:
    """A table's full row/column boundary set, for repeated cell lookups."""

    page: object
    columns: list[float]
    rows: list[float]

    @classmethod
    def detect(cls, page, x_min, x_max, y_min, y_max) -> "Grid":
        columns = column_boundaries(page, x_min, x_max, y_min, y_max)
        rows = row_boundaries(page, x_min, x_max, y_min, y_max)
        return cls(page=page, columns=columns, rows=rows)

    @property
    def num_columns(self) -> int:
        return len(self.columns) - 1

    @property
    def num_rows(self) -> int:
        return len(self.rows) - 1

    def cell(self, row: int, col: int) -> str:
        return cell_text(
            self.page,
            self.columns[col],
            self.columns[col + 1],
            self.rows[row],
            self.rows[row + 1],
        )

    def column_of(self, x0: float) -> int:
        return slot_index(self.columns, x0)

    def row_of(self, top: float) -> int:
        return slot_index(self.rows, top)
