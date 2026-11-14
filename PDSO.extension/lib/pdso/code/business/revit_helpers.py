# -*- coding: utf-8 -*-
"""Revit helpers for the UI-free PDSO services.

All lengths are decimal feet and all areas are square feet (Revit internal units).
"""

# System Modules
import System
from System.Collections.Generic import List

# 3rd Party Modules
from pyrevit import DB

# Custom Modules
from .errors import PDSOError


def to_element_id(value):
    """Make an ElementId from an int (or a string of digits)."""
    try:
        return DB.ElementId(System.Int64(int(value)))
    except (TypeError, ValueError):
        raise PDSOError("Not a valid element id: {!r}".format(value))


def id_value(element_id):
    """Get the integer value of an ElementId."""
    return int(element_id.Value)


def id_list(values):
    """Make a .NET List[ElementId] from ints or ElementIds."""
    items = [
        value if isinstance(value, DB.ElementId) else to_element_id(value)
        for value in values
    ]
    return List[DB.ElementId](items)


def get_element(doc, value, expected_type=None, label="Element"):
    """Get an element by id, or raise PDSOError(404)."""
    element = doc.GetElement(to_element_id(value))
    if element is None or (expected_type and not isinstance(element, expected_type)):
        raise PDSOError("{} {} not found.".format(label, value), 404)
    return element


def all_levels(doc):
    """Get all Levels, lowest first."""
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    return sorted(levels, key=lambda level: level.Elevation)


def find_level(doc, reference):
    """Find a Level by element id (int) or by name."""
    if reference is None:
        raise PDSOError("A level is required (name or id).")
    levels = all_levels(doc)
    text = str(reference)
    for level in levels:
        if level.Name == text:
            return level
    if text.lstrip("-").isdigit():
        for level in levels:
            if id_value(level.Id) == int(text):
                return level
    raise PDSOError(
        "Level '{}' not found. Levels: {}".format(
            reference, ", ".join(level.Name for level in levels)
        ),
        404,
    )


def level_info(level):
    """Describe a Level as a dict."""
    return {
        "id": id_value(level.Id),
        "name": level.Name,
        "elevation": level.Elevation,
    }


def point_in_polygon(x, y, polygon):
    """Ray-casting test. ``polygon`` is a list of (x, y)."""
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / float(yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def polygon_area(polygon):
    """Signed area of a polygon (positive if counter-clockwise)."""
    total = 0.0
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i][0], polygon[i][1]
        x2, y2 = polygon[(i + 1) % count][0], polygon[(i + 1) % count][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def polygon_interior_point(polygon):
    """Find a point that is inside the polygon (for concave shapes too)."""
    count = len(polygon)
    cx = sum(point[0] for point in polygon) / float(count)
    cy = sum(point[1] for point in polygon) / float(count)
    if point_in_polygon(cx, cy, polygon):
        return cx, cy
    # Try the centroid of each triangle made of three consecutive vertices
    for i in range(count):
        a, b, c = polygon[i - 1], polygon[i], polygon[(i + 1) % count]
        tx = (a[0] + b[0] + c[0]) / 3.0
        ty = (a[1] + b[1] + c[1]) / 3.0
        if point_in_polygon(tx, ty, polygon):
            return tx, ty
    raise PDSOError("Cannot find a point inside the outline.")


class _FailureHandler(DB.IFailuresPreprocessor):
    """Deletes warnings and rolls back on errors, so that no dialog opens."""

    def __init__(self):
        self.warnings = []
        self.errors = []

    def PreprocessFailures(self, failures_accessor):
        has_error = False
        for failure in failures_accessor.GetFailureMessages():
            text = failure.GetDescriptionText()
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                self.warnings.append(text)
                failures_accessor.DeleteWarning(failure)
            else:
                self.errors.append(text)
                has_error = True
        if has_error:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


class Transaction(object):
    """Transaction context manager that never opens a Revit dialog.

    Revit warnings are deleted and collected in ``warnings``. A Revit error
    rolls the transaction back and raises PDSOError. An exception in the
    ``with`` block rolls the transaction back.

    Example Usage:
        with Transaction(doc, "PDSO - Create Area") as tx:
            ...
        print(tx.warnings)
    """

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.warnings = []
        self._handler = _FailureHandler()
        self._transaction = None

    def __enter__(self):
        self._transaction = DB.Transaction(self.doc, self.name)
        options = self._transaction.GetFailureHandlingOptions()
        options.SetFailuresPreprocessor(self._handler)
        options.SetForcedModalHandling(False)
        options.SetClearAfterRollback(True)
        self._transaction.SetFailureHandlingOptions(options)
        self._transaction.Start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if exc_type is not None:
            if self._transaction.HasStarted() and not self._transaction.HasEnded():
                self._transaction.RollBack()
            return False
        status = self._transaction.Commit()
        self.warnings = list(self._handler.warnings)
        if status != DB.TransactionStatus.Committed:
            raise PDSOError(
                "Revit rejected '{}': {}".format(
                    self.name,
                    "; ".join(self._handler.errors) or str(status),
                ),
                409,
            )
        return False


class TransactionGroup(object):
    """Group of transactions that is one undo step.

    An exception in the ``with`` block rolls back all transactions of the group.

    Example Usage:
        with TransactionGroup(doc, "PDSO - Create Area"):
            with Transaction(doc, "Draw outline"):
                ...
            with Transaction(doc, "Place area"):
                ...
    """

    def __init__(self, doc, name):
        self._group = DB.TransactionGroup(doc, name)

    def __enter__(self):
        self._group.Start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if exc_type is not None:
            if self._group.HasStarted() and not self._group.HasEnded():
                self._group.RollBack()
            return False
        self._group.Assimilate()
        return False
