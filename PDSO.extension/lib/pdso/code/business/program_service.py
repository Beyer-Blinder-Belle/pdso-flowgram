# -*- coding: utf-8 -*-
"""PDSO Program Service - create, read, change, delete and select programs.

A "program" (a room type or unit type in a building program) is one Data Silo:
an instance of the Data Silo family that holds the PDSO parameters. A Data Silo
gets its areas from the Areas that contain its location point on its level.

All lengths are decimal feet and all areas are square feet.

Example Usage:
    program = create_program(doc, "L1", 20.0, 15.0, {"uid": "A1-01-1B"})
    update_program(doc, program["id"], {"program_type": "Residential"})
"""

# Python standard libraries
import re

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from ..data.shared_parameter_constants import standard_pdso_parameters
from . import config_service, parameter_service
from . import parameter_registry_service
from .bootstrap_service import find_family
from .errors import PDSOError
from .revit_helpers import (
    Transaction,
    find_level,
    get_element,
    id_list,
    id_value,
    point_in_polygon,
    polygon_area,
    polygon_interior_point,
)

logger = script.get_logger()



def _fields(doc):
    """Field name -> parameter_service.Field, for the tracked parameters."""
    fields = parameter_service.by_field(doc)
    if not fields:
        raise PDSOError(
            "The parameters of the model are not set up. Run the bootstrap first.",
            409,
        )
    return fields


def field_schema(doc):
    """Describe the fields of a program.

    Returns:
        list[dict]: One dict per field with keys "field", "parameter", "type"
            (see GET /pdso/parameters), "editable" and "standard" (False for a
            custom parameter)
    """
    return [
        {
            "field": parameter.field,
            "parameter": parameter.name,
            "type": parameter.parameter_type,
            "editable": not parameter.managed and not parameter.missing,
            "standard": parameter.standard,
        }
        for parameter in parameter_service.all_parameters(doc, include_missing=True)
    ]


# =============================================================================
# Read
# =============================================================================


def _require_config(doc):
    config = config_service.get_config(doc)
    if not config["exists"]:
        raise PDSOError(
            "The model has no PDSO configuration. Run the bootstrap first.", 409
        )
    return config


def _silo_instances(doc, family_name):
    return [
        instance
        for instance in DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .OfCategory(DB.BuiltInCategory.OST_GenericModel)
        if instance.Symbol.Family.Name == family_name
    ]


def _read_parameter(element, parameter_name):
    """Read a built-in PDSO parameter by its name.

    The GUID comes from the tracked parameters (a model can use an adopted
    shared parameter with a different GUID), then from the built-in catalog.
    """
    doc = element.Document
    tracked = parameter_registry_service.get_registry(doc).get(parameter_name)
    definition = tracked or standard_pdso_parameters[parameter_name]
    if definition.parameter_guid is None:
        return None
    return _read_guid(element, definition.parameter_guid)


def _read_guid(element, parameter_guid):
    parameter = element.get_Parameter(parameter_guid)
    if parameter is None:
        return None
    if parameter.StorageType == DB.StorageType.String:
        return parameter.AsString() or ""
    if parameter.StorageType == DB.StorageType.Double:
        return parameter.AsDouble()
    if parameter.StorageType == DB.StorageType.Integer:
        return parameter.AsInteger()
    return parameter.AsValueString()


def _location(element):
    location = element.Location
    point = getattr(location, "Point", None)
    if point is None:
        return None
    return {"x": point.X, "y": point.Y, "z": point.Z}


def _summary(doc, element, parameters=None):
    if parameters is None:
        parameters = parameter_service.all_parameters(doc, include_missing=True)
    level = doc.GetElement(element.LevelId)
    data = {
        "id": id_value(element.Id),
        "level": level.Name if level is not None else None,
        "location": _location(element),
        "pinned": element.Pinned,
    }
    for parameter in parameters:
        data[parameter.field] = (
            None if parameter.missing else _read_guid(element, parameter.guid)
        )
    return data


def get_program_element(doc, program_id):
    """Get a Data Silo by element id, or raise PDSOError(404)."""
    config = _require_config(doc)
    element = get_element(doc, program_id, DB.FamilyInstance, "Program")
    if element.Symbol.Family.Name != config["data_silo_family_name"]:
        raise PDSOError(
            "Element {} is not a program (Data Silo).".format(program_id), 404
        )
    return element


def find_program_by_uid(doc, uid):
    """Find Data Silos by "PDSO - Program UID"."""
    config = _require_config(doc)
    return [
        element
        for element in _silo_instances(doc, config["data_silo_family_name"])
        if _read_parameter(element, "PDSO - Program UID") == uid
    ]


def resolve_program(doc, reference):
    """Get a Data Silo by element id (int) or by UID (str)."""
    text = str(reference)
    if text.isdigit():
        return get_program_element(doc, int(text))
    matches = find_program_by_uid(doc, text)
    if not matches:
        raise PDSOError("No program has the UID '{}'.".format(text), 404)
    if len(matches) > 1:
        raise PDSOError(
            "{} programs have the UID '{}'. Use the element id: {}".format(
                len(matches), text, ", ".join(str(id_value(m.Id)) for m in matches)
            ),
            409,
        )
    return matches[0]


def list_programs(doc, filters=None):
    """List the programs (Data Silos).

    Args:
        doc: Revit Document
        filters (dict): Optional. Field name -> value. A program is in the
            result if each given field has the given value. "level" filters by
            level name.

    Returns:
        list[dict]: One dict per program (see describe_program for the keys,
            without "areas" and "program_volumes")
    """
    config = _require_config(doc)
    filters = dict(
        (key, value) for key, value in (filters or {}).items() if value is not None
    )
    parameters = parameter_service.all_parameters(doc, include_missing=True)
    known = set(parameter.field for parameter in parameters)
    for key in filters:
        if key != "level" and key not in known:
            raise PDSOError("Unknown filter field: '{}'".format(key))

    programs = []
    for element in _silo_instances(doc, config["data_silo_family_name"]):
        data = _summary(doc, element, parameters)
        if all(str(data.get(key)) == str(value) for key, value in filters.items()):
            programs.append(data)
    return sorted(programs, key=lambda item: (item["level"] or "", item["uid"] or ""))


def summarize_program_types(doc, group_by="program_type"):
    """Count the programs and add their areas for each value of a field.

    Returns:
        list[dict]: One dict per value with keys "value", "count", "gsf", "nsf",
            "zfa" and "uids"
    """
    if group_by not in _fields(doc):
        raise PDSOError("Unknown field: '{}'".format(group_by))
    groups = {}
    for program in list_programs(doc):
        key = program.get(group_by)
        key = "" if key is None else key
        group = groups.setdefault(
            key,
            {"value": key, "count": 0, "gsf": 0.0, "nsf": 0.0, "zfa": 0.0, "uids": []},
        )
        group["count"] += 1
        for area_field in ("gsf", "nsf", "zfa"):
            group[area_field] += program.get(area_field) or 0.0
        group["uids"].append(program.get("uid"))
    return sorted(groups.values(), key=lambda group: str(group["value"]))


def _outline(area):
    """Outer loop of an Area as a list of (x, y), or None."""
    loops = area.GetBoundarySegments(DB.SpatialElementBoundaryOptions())
    if loops is None or loops.Count == 0:
        return None
    best = None
    for loop in loops:
        points = []
        for segment in loop:
            start = segment.GetCurve().GetEndPoint(0)
            points.append((start.X, start.Y))
        if len(points) >= 3 and (best is None or len(points) > len(best)):
            best = points
    return best


def containing_areas(doc, element):
    """Find the Area of each configured scheme that contains a Data Silo.

    Returns:
        dict: Area scheme name -> {"id", "number", "name", "area"} or None
    """
    config = _require_config(doc)
    location = _location(element)
    result = {}
    scheme_names = set(config["area_scheme_mappings"].values())
    for name in scheme_names:
        result[name] = None
    if location is None:
        return result
    for area in DB.FilteredElementCollector(doc).OfCategory(
        DB.BuiltInCategory.OST_Areas
    ):
        if area.LevelId != element.LevelId:
            continue
        try:
            scheme_name = area.AreaScheme.Name
        except Exception:
            continue
        if scheme_name not in scheme_names or result[scheme_name] is not None:
            continue
        outline = _outline(area)
        if outline and point_in_polygon(location["x"], location["y"], outline):
            name_parameter = area.get_Parameter(DB.BuiltInParameter.ROOM_NAME)
            result[scheme_name] = {
                "id": id_value(area.Id),
                "number": area.Number,
                "name": name_parameter.AsString() if name_parameter else None,
                "area": area.Area,
            }
    return result


def describe_program(doc, reference):
    """Describe one program completely.

    Returns:
        dict: "id", "level", "location", "pinned", one key per field of FIELDS,
            "areas" (the containing Area of each configured scheme) and
            "program_volumes" (the Program Volume family and its instances;
            each instance has "id", "level" and "fields": its parameter values)
    """
    element = resolve_program(doc, reference)
    config = _require_config(doc)
    parameters = parameter_service.all_parameters(doc, include_missing=True)
    data = _summary(doc, element, parameters)
    data["areas"] = containing_areas(doc, element)

    family_name = "{}{}".format(
        config["program_volume_family_name_prefix"], data["uid"] or ""
    )
    family = find_family(doc, family_name) if data["uid"] else None
    instances = []
    if family is not None:
        for instance in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
            if instance.Symbol.Family.Id == family.Id:
                level = doc.GetElement(instance.LevelId)
                instances.append(
                    {
                        "id": id_value(instance.Id),
                        "level": level.Name if level is not None else None,
                        "fields": dict(
                            (
                                parameter.field,
                                None
                                if parameter.missing
                                else _read_guid(instance, parameter.guid),
                            )
                            for parameter in parameters
                        ),
                    }
                )
    data["program_volumes"] = {
        "family_name": family_name,
        "family_loaded": family is not None,
        "instances": instances,
    }
    return data


# =============================================================================
# Write
# =============================================================================


def _coerce(parameter, value):
    kind = parameter.parameter_type
    if kind in ("number", "area", "length"):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise PDSOError("Field '{}' must be a number.".format(parameter.field))
    if kind == "integer":
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            raise PDSOError("Field '{}' must be an integer.".format(parameter.field))
    if kind == "yesno":
        if isinstance(value, bool):
            return 1 if value else 0
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return 1
        if text in ("0", "false", "no", "off", ""):
            return 0
        raise PDSOError("Field '{}' must be true or false.".format(parameter.field))
    return "" if value is None else str(value)


def _normalize_fields(doc, fields):
    """Accept short field names or full parameter names. Return short names."""
    known = _fields(doc)
    field_by_name = dict((parameter.name, field) for field, parameter in known.items())
    normalized = {}
    for key, value in (fields or {}).items():
        field = field_by_name.get(key, key)
        if field not in known:
            raise PDSOError(
                "Unknown program field: '{}'. Fields: {}".format(
                    key,
                    ", ".join(
                        parameter.field
                        for parameter in parameter_service.all_parameters(
                            doc, include_missing=True
                        )
                    ),
                )
            )
        parameter = known[field]
        if parameter.managed:
            raise PDSOError(
                "Field '{}' is written by the sync and cannot be set.".format(field)
            )
        if parameter.missing:
            raise PDSOError(
                "Parameter '{}' is not in the model. Create it again.".format(
                    parameter.name
                ),
                409,
            )
        normalized[field] = _coerce(parameter, value)
    return normalized


def _check_uid(doc, config, uid, own_id=None):
    if not uid:
        raise PDSOError("A program must have a 'uid'.")
    pattern = config.get("uid_pattern")
    if pattern and not re.match(pattern, uid):
        raise PDSOError(
            "UID '{}' does not match the configured pattern {}".format(uid, pattern)
        )
    for other in find_program_by_uid(doc, uid):
        if own_id is None or id_value(other.Id) != own_id:
            raise PDSOError(
                "UID '{}' is in use by program {}.".format(uid, id_value(other.Id)),
                409,
            )


def _write_fields(doc, element, fields):
    known = _fields(doc) if fields else {}
    for field, value in fields.items():
        definition = known[field].definition
        parameter = element.get_Parameter(definition.parameter_guid)
        if parameter is None:
            raise PDSOError(
                "Parameter '{}' is not on the element. Run the bootstrap.".format(
                    definition.parameter_name
                ),
                409,
            )
        if parameter.IsReadOnly:
            raise PDSOError(
                "Parameter '{}' is read-only.".format(definition.parameter_name), 409
            )
        parameter.Set(value)


def _data_silo_symbol(doc, config):
    family = find_family(doc, config["data_silo_family_name"])
    if family is None:
        raise PDSOError(
            "The Data Silo family '{}' is not loaded. Run the bootstrap.".format(
                config["data_silo_family_name"]
            ),
            409,
        )
    return doc.GetElement(list(family.GetFamilySymbolIds())[0])


def _place_program(doc, symbol, level_element, x, y):
    """Place one Data Silo. Call this in a transaction."""
    if not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()
    point = DB.XYZ(float(x), float(y), level_element.ProjectElevation)
    element = doc.Create.NewFamilyInstance(
        point, symbol, level_element, DB.Structure.StructuralType.NonStructural
    )
    # The instance is hosted on the level. Revit reads the Z of the point as
    # an offset from the level, so set the offset to zero (else a program on
    # a level that is not at elevation 0 is above its plan view range).
    for built_in in (
        DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM,
        DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM,
    ):
        offset = element.get_Parameter(built_in)
        if offset is not None and not offset.IsReadOnly:
            offset.Set(0.0)
            break
    doc.Regenerate()
    return element


def create_program(doc, level, x, y, fields):
    """Place a Data Silo and set its fields.

    Args:
        doc: Revit Document
        level: Level name or id
        x (float): X coordinate (feet)
        y (float): Y coordinate (feet)
        fields (dict): Field values. "uid" is required.

    Returns:
        dict: The new program (see describe_program)
    """
    config = _require_config(doc)
    fields = _normalize_fields(doc, fields)
    _check_uid(doc, config, fields.get("uid"))
    level_element = find_level(doc, level)

    symbol = _data_silo_symbol(doc, config)

    with Transaction(doc, "PDSO - Create Program"):
        element = _place_program(doc, symbol, level_element, x, y)
        _write_fields(doc, element, fields)
    return describe_program(doc, id_value(element.Id))


def _area_placement(doc, area_id):
    """Level and interior point of an Area, for a Data Silo.

    Returns:
        tuple: (Level, x, y)
    """
    # Import here: area_drafting_service imports this module.
    from . import area_drafting_service

    area = area_drafting_service.get_area(doc, area_id)
    loops = [
        loop["points"]
        for loop in area_drafting_service._loops(area)
        if len(loop["points"]) >= 3
    ]
    if not loops:
        raise PDSOError(
            "Area {} has no outline (it is not placed or not enclosed).".format(
                area_id
            )
        )
    outer = max(loops, key=lambda points: abs(polygon_area(points)))
    holes = [points for points in loops if points is not outer]

    def usable(x, y):
        return point_in_polygon(x, y, outer) and not any(
            point_in_polygon(x, y, hole) for hole in holes
        )

    candidates = []
    try:
        candidates.append(polygon_interior_point(outer))
    except PDSOError:
        pass
    count = len(outer)
    for i in range(count):
        a, b, c = outer[i - 1], outer[i], outer[(i + 1) % count]
        candidates.append(((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0))
    for x, y in candidates:
        if usable(x, y):
            return doc.GetElement(area.LevelId), x, y
    raise PDSOError(
        "Cannot find a point inside the outline of Area {}.".format(area_id)
    )


def create_programs(doc, items):
    """Place many Data Silos in one transaction.

    Args:
        doc: Revit Document
        items (list): One dict per program. Either {"area_id", "fields"}: the
            Data Silo goes to a point inside the Area, on the level of the
            Area. Or {"level", "x", "y", "fields"}. "uid" is required in
            "fields".

    Returns:
        dict: {"created": [programs]} (see list_programs for the keys). If one
            item is not valid, no program is placed and the error names the
            item.
    """
    if not isinstance(items, list) or not items:
        raise PDSOError("'items' must be a list with at least one program.")
    config = _require_config(doc)
    symbol = _data_silo_symbol(doc, config)

    placements = []
    uids = set()
    for index, item in enumerate(items):
        label = "items[{}]".format(index)
        if isinstance(item, dict) and item.get("area_id") is not None:
            label = "{} (area_id {})".format(label, item.get("area_id"))
        try:
            if not isinstance(item, dict):
                raise PDSOError("An item must be an object.")
            fields = _normalize_fields(doc, item.get("fields"))
            uid = fields.get("uid")
            _check_uid(doc, config, uid)
            if uid in uids:
                raise PDSOError("UID '{}' is in the list two times.".format(uid), 409)
            uids.add(uid)
            if item.get("area_id") is not None:
                level_element, x, y = _area_placement(doc, item["area_id"])
            else:
                if item.get("x") is None or item.get("y") is None:
                    raise PDSOError("Give 'area_id', or 'level', 'x' and 'y'.")
                level_element = find_level(doc, item.get("level"))
                x, y = float(item["x"]), float(item["y"])
        except PDSOError as error:
            raise PDSOError("{}: {}".format(label, error), 400)
        except (TypeError, ValueError) as error:
            raise PDSOError("{}: {}".format(label, error), 400)
        placements.append((level_element, x, y, fields))

    created_ids = []
    with Transaction(doc, "PDSO - Create Programs"):
        for level_element, x, y, fields in placements:
            element = _place_program(doc, symbol, level_element, x, y)
            _write_fields(doc, element, fields)
            created_ids.append(element.Id)
    parameters = parameter_service.all_parameters(doc, include_missing=True)
    return {
        "created": [
            _summary(doc, doc.GetElement(element_id), parameters)
            for element_id in created_ids
        ]
    }


def update_program(doc, reference, fields=None, x=None, y=None):
    """Change the fields and the position of a program.

    Args:
        doc: Revit Document
        reference: Element id or UID of the program
        fields (dict): Field values to change (optional)
        x (float): New X coordinate in feet (optional)
        y (float): New Y coordinate in feet (optional)

    Returns:
        dict: The program after the change (see describe_program)
    """
    element = resolve_program(doc, reference)
    config = _require_config(doc)
    fields = _normalize_fields(doc, fields)
    if "uid" in fields:
        _check_uid(doc, config, fields["uid"], own_id=id_value(element.Id))
    if not fields and x is None and y is None:
        raise PDSOError("Give fields or a position to change.")

    with Transaction(doc, "PDSO - Update Program"):
        _write_fields(doc, element, fields)
        if x is not None or y is not None:
            current = element.Location.Point
            target = DB.XYZ(
                current.X if x is None else float(x),
                current.Y if y is None else float(y),
                current.Z,
            )
            was_pinned = element.Pinned
            if was_pinned:
                element.Pinned = False
            DB.ElementTransformUtils.MoveElement(doc, element.Id, target - current)
            if was_pinned:
                element.Pinned = True
    return describe_program(doc, id_value(element.Id))


def update_programs(doc, filters, fields):
    """Set the same field values on all programs that match the filters.

    Returns:
        dict: {"updated": [element ids]}
    """
    fields = _normalize_fields(doc, fields)
    if not fields:
        raise PDSOError("Give the fields to change.")
    if "uid" in fields:
        raise PDSOError("A UID is unique. Change it on one program at a time.")
    if not filters:
        raise PDSOError("Give filters. To change one program, use its id.")
    targets = list_programs(doc, filters)
    with Transaction(doc, "PDSO - Update Programs"):
        for program in targets:
            _write_fields(doc, get_program_element(doc, program["id"]), fields)
    return {"updated": [program["id"] for program in targets]}


def delete_program(doc, reference):
    """Delete a program (Data Silo). Its Program Volumes stay until the next sync."""
    element = resolve_program(doc, reference)
    program_id = id_value(element.Id)
    uid = _read_parameter(element, "PDSO - Program UID")
    with Transaction(doc, "PDSO - Delete Program"):
        if element.Pinned:
            element.Pinned = False
        doc.Delete(element.Id)
    return {"deleted": program_id, "uid": uid}


# =============================================================================
# Selection
# =============================================================================


def select_elements(uidoc, element_ids, zoom=True):
    """Select elements in the Revit window and zoom to them.

    Returns:
        dict: {"selected": [element ids]}
    """
    doc = uidoc.Document
    for element_id in element_ids:
        get_element(doc, element_id)
    ids = id_list(element_ids)
    uidoc.Selection.SetElementIds(ids)
    if zoom and ids.Count > 0:
        try:
            uidoc.ShowElements(ids)
        except Exception as error:
            logger.debug("ShowElements failed: {}".format(error))
    return {"selected": [int(element_id) for element_id in element_ids]}


def get_selection(uidoc):
    """Describe the elements that are selected in the Revit window.

    Returns:
        list[dict]: One dict per element with keys "id", "category", "name" and
            "kind" ("program", "area" or "other")
    """
    doc = uidoc.Document
    config = config_service.get_config(doc)
    family_name = config.get("data_silo_family_name")
    selection = []
    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if element is None:
            continue
        kind = "other"
        if isinstance(element, DB.Area):
            kind = "area"
        elif (
            isinstance(element, DB.FamilyInstance)
            and element.Symbol.Family.Name == family_name
        ):
            kind = "program"
        selection.append(
            {
                "id": id_value(element_id),
                "category": element.Category.Name if element.Category else None,
                "name": element.Name,
                "kind": kind,
            }
        )
    return selection
