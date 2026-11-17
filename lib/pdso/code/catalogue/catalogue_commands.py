# -*- coding: utf-8 -*-
"""Shared steps of the Unit Layout Catalogue buttons (no window code)."""

from pyrevit import DB, HOST_APP, revit
from rpw.utils.dotnet import List

from pdso.code.business.errors import PDSOError
from pdso.code.business.revit_helpers import id_value, to_element_id
from pdso.code.catalogue import catalogue_api_service as catalogue
from pdso.code.catalogue import catalogue_windows as windows
from pdso.code.catalogue import revit_layout_service as layout_service
from pdso.code.catalogue.harvest_features import DATA_SILO, pdso_kinds
from pdso.code.catalogue.layout_geometry import point_in_polygon


def owner():
    """Window handle of Revit, so that a window stays in front of it."""
    try:
        return HOST_APP.uiapp.MainWindowHandle
    except Exception:
        return None


def _plan_point(element):
    """(x, y, level id or None) of an element, or None."""
    location = element.Location
    if isinstance(location, DB.LocationPoint):
        point = location.Point
    elif isinstance(location, DB.LocationCurve):
        point = location.Curve.Evaluate(0.5, True)
    else:
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        point = (box.Min + box.Max) * 0.5
    level_id = None
    try:
        if isinstance(element, DB.FamilyInstance):
            level_id = id_value(layout_service._instance_level_id(element))
        else:
            level_id = id_value(element.LevelId)
    except Exception:
        level_id = None
    if level_id is not None and level_id < 0:
        level_id = None
    return (point.X, point.Y, level_id)


def selected_target_id():
    """Area id of the one selected unit, or None.

    The selection is a unit's Area or its PDSO data silo. A unit can get a
    layout; it does not have to be in the catalogue.
    """
    doc = revit.doc
    found = []
    kind = pdso_kinds(doc)
    for element_id in revit.uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if isinstance(element, DB.Area) and element.Area > 0:
            found.append(id_value(element.Id))
        elif element is not None and kind(element) == DATA_SILO:
            try:
                found.append(id_value(catalogue.get_unit(doc, id_value(element.Id)).Id))
            except PDSOError:
                continue
    found = sorted(set(found))
    return found[0] if len(found) == 1 else None


class UnitIndex(object):
    """The units of the model (Areas with a data silo) with their plan data, read one time."""

    def __init__(self):
        self.units = catalogue.units_overview(revit.doc)
        counts = {}
        for unit in self.units:
            counts[unit["uid"]] = counts.get(unit["uid"], 0) + 1
        # A UID that two units have (preflight warns about it) selects nothing
        self.by_uid = dict(
            (unit["uid"], unit)
            for unit in self.units
            if unit["uid"] and counts[unit["uid"]] == 1
        )

    def uid_of_selection(self):
        """Program UID of the unit of the selection, or "".

        The selection can be the unit's Area or data silo, or objects that are
        in the unit (furniture, a door, a tag). All of them must be in one unit.
        """
        uids = []
        for element_id in revit.uidoc.Selection.GetElementIds():
            element = revit.doc.GetElement(element_id)
            if element is None:
                continue
            found = []
            if isinstance(element, DB.Area):
                found = [u["uid"] for u in self.units if u["id"] == id_value(element.Id)]
            if not found:
                located = _plan_point(element)
                if located is None:
                    continue
                x, y, level_id = located
                found = [
                    unit["uid"]
                    for unit in self.units
                    if (level_id is None or unit["level_id"] == level_id)
                    and point_in_polygon((x, y), unit["outline"])
                ]
            if len(found) != 1:
                continue
            if found[0] not in uids:
                uids.append(found[0])
        if len(uids) == 1 and uids[0] in self.by_uid:
            return uids[0]
        return ""

    def describe(self, uid):
        """Data of the unit with this Program UID for a window, or None."""
        unit = self.by_uid.get(uid)
        if unit is None:
            return None
        described = dict(unit)
        described["items"] = unit["item_count"]
        return described


def place(unit, layout_id, clear_first):
    """Place a layout, select the placed elements, show the result."""
    doc = revit.doc
    try:
        result = catalogue.place_layout(doc, unit["id"], layout_id, clear_first)
    except PDSOError as error:
        windows.show_message("The layout was not placed", [(str(error), "bad")], owner())
        return None
    deleted = result["deleted"]
    ids = List[type(to_element_id("1"))]()
    for element_id in result["placed"]:
        ids.Add(to_element_id(str(element_id)))
    revit.uidoc.Selection.SetElementIds(ids)
    missing = sum(result["missing"].values())
    lines = [
        (
            "{} items placed in Area {} ({}). They are selected.".format(
                len(result["placed"]), unit["number"], result["placement"]["description"]
            ),
            "good",
        )
    ]
    if clear_first:
        lines.append(("{} items that were in the unit were deleted first.".format(deleted), None))
    if result.get("loaded_families"):
        lines.append(
            (
                "Families loaded into this model: {}".format(
                    ", ".join(sorted(result["loaded_families"]))
                ),
                None,
            )
        )
    if missing:
        names = ", ".join(sorted(result["missing"]))
        lines.append(
            ("{} items not placed. Family not loaded: {}".format(missing, names), "warn")
        )
    for warning in result["warnings"][:3]:
        lines.append((warning, "warn"))
    if result["tagged_programs"]:
        lines.append(
            ("The layout id is on {} PDSO data silo(s).".format(len(result["tagged_programs"])), None)
        )
    windows.show_message("Layout placed", lines, owner())
    return result


def match_right(match, score=None):
    lines = [
        "Area match: {}".format(windows.percent(match["area_match"])),
        "Shape match: {}".format(windows.percent(match["shape_match"])),
        "similarity {:.3f}".format(match["similarity"]),
    ]
    if score is not None:
        lines[2] = "fit score {:.3f}".format(score)
    return lines
