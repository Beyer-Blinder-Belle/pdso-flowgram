# -*- coding: utf-8 -*-
"""Unit Layout Catalogue without a UI, for the HTTP API and the MCP server.

A unit is an Area with a PDSO data silo inside (by default in the scheme that
feeds PDSO - GSF). Functions name a unit by its Area id, its data silo's id or
its Program UID; Revit Rooms are never used. The Harvest and Pull buttons pick
the unit and ask for names; these functions take the same inputs as arguments
and return plain data, through the same harvest, store, recommend and create
code as the buttons.

All lengths are decimal feet. All areas are square feet.

Example Usage:
    entry = harvest_layout(doc, 1234, name="Studio", program="Studio Unit")
    matches = find_similar_units(doc, entry["id"])
    picture_data = preview_layout_in_unit(doc, matches["units"][0]["id"], entry["id"])
    place_layout(doc, matches["units"][0]["id"], entry["id"])
"""

import base64
import math
import os
from datetime import datetime

from rpw import DB

from pdso.code.business.errors import PDSOError
from pdso.code.business.revit_helpers import (
    Transaction,
    find_level,
    id_list,
    id_value,
    to_element_id,
)
from pdso.code.catalogue import revit_layout_service as layout_service
from pdso.code.catalogue.catalogue_settings import catalogue_folders, get_catalogue_folder
from pdso.code.catalogue.harvest_features import (
    DATA_SILO,
    boundary_edges,
    pdso_kinds,
    pdso_value,
)
from pdso.code.data import parameter_access
from pdso.code.catalogue.harvest_image import UnitImager
from pdso.code.catalogue.layout_entry import is_entry_id, slugify
from pdso.code.catalogue.catalogue_store import (
    ENTRY_EXTENSION,
    IMAGE_EXTENSION,
    SKETCH_EXTENSION,
    assign_catalogue,
    catalogue_lock,
    catalogue_numbers,
    default_local_folder,
    entry_to_svg,
    load_catalogue,
    remove_previous,
    sample_folder,
    save_entry,
)
from pdso.code.catalogue.layout_geometry import (
    ORIENTATIONS,
    distance_to_polygon,
    orient_angle,
    orient_point,
    orient_size,
    outline_overlap,
    point_in_or_near_polygon,
    point_in_polygon,
    rotate,
)
from pdso.code.catalogue.layout_recommender import (
    FIT_TOLERANCE,
    Placement,
    oriented_items,
    oriented_outline,
    recommend,
    refine_placement,
)

# The area parameter whose scheme holds the units (see unit_scheme)
GSF_PARAMETER = "PDSO - GSF"

# Number of sample points on each side of the grid that compares two outlines
SHAPE_GRID = 24


# =============================================================================
# Catalogue files
# =============================================================================


def _entries():
    entries, errors = load_catalogue(catalogue_folders())
    return entries, errors


def _entry_folder(entry_id):
    """Folder that holds an entry's JSON file. Later folders win."""
    found = None
    for folder in catalogue_folders():
        if os.path.exists(os.path.join(folder, entry_id + ENTRY_EXTENSION)):
            found = folder
    return found


def get_entry(entry_id):
    """Get a catalogue entry by id, or raise PDSOError(404)."""
    entries, _ = _entries()
    for entry in entries:
        if entry.id == entry_id:
            return entry
    raise PDSOError(
        "No layout has the id '{}'. Use the layout list.".format(entry_id), 404
    )


def entry_summary(entry):
    """Describe an entry without its items and boundary."""
    counts = {}
    for item in entry.items:
        key = "{} : {}".format(item.family, item.type)
        counts[key] = counts.get(key, 0) + 1
    folder = _entry_folder(entry.id)
    return {
        "id": entry.id,
        "name": entry.name,
        "program": entry.program,
        "project_type": entry.project_type,
        "width": entry.width,
        "depth": entry.depth,
        "area": entry.area,
        "item_count": len(entry.items),
        "families": counts,
        "source": entry.source,
        "notes": entry.notes,
        "file": os.path.join(folder, entry.id + ENTRY_EXTENSION) if folder else None,
        "image": _entry_image(folder, entry.id),
        "catalogue": _catalogue_group(entry, folder),
    }


def _catalogue_group(entry, folder):
    """The entry's catalogue group, with the folder that numbered it.

    Each folder numbers its own shapes, and the catalogue reads several
    folders, so a number means one shape only within its folder ("folder").
    """
    group = (entry.features or {}).get("catalogue")
    if not group:
        return None
    group = dict(group)
    group["folder"] = folder_label(folder)
    return group


def folder_label(folder):
    """A short name for a catalogue folder: "sample", "local" or its own name."""
    if not folder:
        return None
    path = os.path.normcase(os.path.abspath(folder))
    if path == os.path.normcase(os.path.abspath(sample_folder())):
        return "sample"
    if path == os.path.normcase(os.path.abspath(default_local_folder())):
        return "local"
    return os.path.basename(os.path.normpath(folder)) or folder


def _entry_image(folder, entry_id):
    """Path of the plan image that Harvest saved beside the entry, or None."""
    if not folder:
        return None
    path = os.path.join(folder, entry_id + IMAGE_EXTENSION)
    return path if os.path.isfile(path) else None


def list_layouts(program=None, project_type=None):
    """List the layouts in the catalogue.

    Args:
        program (str): Only layouts for this program (optional)
        project_type (str): Only layouts of this project type (optional)

    Returns:
        dict: {"layouts": [summary, ...], "folders": [paths], "errors": [str]}
    """
    entries, errors = _entries()
    wanted_program = (program or "").strip().lower()
    wanted_type = (project_type or "").strip().lower()
    layouts = []
    for entry in entries:
        if wanted_program and entry.program.strip().lower() != wanted_program:
            continue
        if wanted_type and entry.project_type.strip().lower() != wanted_type:
            continue
        layouts.append(entry_summary(entry))
    layouts.sort(key=lambda item: (item["program"], item["name"]))
    return {"layouts": layouts, "folders": catalogue_folders(), "errors": errors}


def get_layout(entry_id):
    """Get one layout completely: the JSON entry, its SVG sketch and its image.

    Returns:
        dict: {"summary", "entry" (the stored JSON), "svg", "image_base64"}.
            image_base64 is the PNG plan image Harvest saved, or None. The
            summary's "image" is that file's path on the Revit computer, which
            a remote client can't open.
    """
    entry = get_entry(entry_id)
    summary = entry_summary(entry)
    image_base64 = None
    if summary["image"]:
        with open(summary["image"], "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read())
        if not isinstance(image_base64, str):
            image_base64 = image_base64.decode("ascii")
    return {
        "summary": summary,
        "entry": entry.to_dict(),
        "svg": entry_to_svg(entry),
        "image_base64": image_base64,
    }


def delete_layout(entry_id):
    """Delete a layout file. A layout in the sample folder cannot be deleted."""
    get_entry(entry_id)
    folder = _entry_folder(entry_id)
    if folder is None or os.path.normcase(folder) == os.path.normcase(sample_folder()):
        raise PDSOError("A sample layout cannot be deleted.", 409)
    removed = []
    for extension in (ENTRY_EXTENSION, SKETCH_EXTENSION, IMAGE_EXTENSION):
        path = os.path.join(folder, entry_id + extension)
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    return {"deleted": entry_id, "files": removed}


# =============================================================================
# Units: Areas with a PDSO data silo inside
# =============================================================================


def unit_scheme(doc):
    """The area scheme of the units: the one that feeds PDSO - GSF."""
    try:
        from pdso.code.business.config_service import get_config

        config = get_config(doc)
        if config.get("exists"):
            mapped = (config.get("area_scheme_mappings") or {}).get(GSF_PARAMETER)
            if mapped:
                return mapped
    except Exception:
        pass
    return GSF_PARAMETER


def get_unit(doc, reference):
    """Get a unit's Area by Area id, by its data silo's id, or by Program UID.

    A data silo or a Program UID gives the Area of unit_scheme that holds the
    silo. Revit Rooms are not units: a Room id is refused.
    """
    text = str(reference).strip()
    if text.lstrip("-").isdigit():
        element = doc.GetElement(to_element_id(text))
        if isinstance(element, DB.Area):
            if element.Area <= 0:
                raise PDSOError(
                    "Area {} is not placed or not enclosed. It has no boundary.".format(text),
                    409,
                )
            return element
        if isinstance(element, DB.Architecture.Room):
            raise PDSOError(
                "Element {} is a Revit Room. PDSO works with Areas: give the id of an "
                "Area or a data silo, or a Program UID.".format(text)
            )
        if element is not None and pdso_kinds(doc)(element) == DATA_SILO:
            return _area_of_silo(doc, element)
    from pdso.code.business.program_service import find_program_by_uid

    silos = find_program_by_uid(doc, text)
    if len(silos) == 1:
        return _area_of_silo(doc, silos[0])
    if len(silos) > 1:
        raise PDSOError(
            "{} programs have the UID '{}'. Use the Area id.".format(len(silos), text), 409
        )
    raise PDSOError(
        "'{}' is not the id of an Area or a data silo, and no program has that "
        "UID.".format(reference),
        404,
    )


def _area_of_silo(doc, silo):
    """The Area of unit_scheme that holds a data silo."""
    from pdso.code.business.program_service import containing_areas

    scheme = unit_scheme(doc)
    found = containing_areas(doc, silo).get(scheme)
    if found is None:
        raise PDSOError(
            "The program (data silo {}) is in no Area of '{}'.".format(
                id_value(silo.Id), scheme
            ),
            404,
        )
    return doc.GetElement(to_element_id(found["id"]))


def _unit_info(element):
    level = layout_service.get_level(element)
    return {
        "id": id_value(element.Id),
        "number": element.Number,
        "name": layout_service.get_name(element),
        "scheme": layout_service._scheme_name(element),
        "level": level.Name if level is not None else None,
        "area": element.Area,
    }


def _unit_instances(element):
    """The free-standing family instances that a harvest of the element takes.

    This uses the same rules as revit_layout_service.harvest.
    """
    doc = element.Document
    outline = layout_service.get_outline(element)
    if len(outline) < 3:
        return []
    level_id = element.LevelId
    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    instances = []
    pdso_kind = pdso_kinds(doc)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        # PDSO data silos and Program Volumes are not furniture. They must
        # not be listed, counted or deleted.
        if pdso_kind(instance) is not None:
            continue
        point = location.Point
        if not point_in_polygon((point.X, point.Y), outline):
            continue
        if layout_service._instance_level_id(instance) != level_id:
            continue
        if instance.SuperComponent is not None:
            continue
        host = instance.Host
        if host is not None and not isinstance(host, DB.Level):
            continue
        if instance.HostFace is not None:
            continue
        instances.append(instance)
    return instances


def list_unit_items(doc, reference):
    """List the free-standing families in a unit (what a harvest takes).

    Returns:
        dict: {"unit": {...}, "items": [{"id", "category", "family", "type",
            "x", "y", "z" (project elevation), "z_offset" (height above the
            level), "rotation"}]}
    """
    element = get_unit(doc, reference)
    level = layout_service.get_level(element)
    items = []
    for instance in _unit_instances(element):
        point = instance.Location.Point
        items.append(
            {
                "id": id_value(instance.Id),
                "category": instance.Category.Name,
                "family": instance.Symbol.Family.Name,
                "type": layout_service.get_type_name(instance.Symbol),
                "x": point.X,
                "y": point.Y,
                "z": point.Z,
                "z_offset": layout_service._z_offset(instance, point, level),
                "rotation": instance.Location.Rotation,
            }
        )
    items.sort(key=lambda item: (item["family"], item["type"], item["x"], item["y"]))
    return {"unit": _unit_info(element), "items": items}


def clear_unit_items(doc, reference):
    """Delete the free-standing families in a unit.

    This removes what a harvest of the unit takes. Use it to make a furnished
    unit empty before a layout is placed.

    Returns:
        dict: {"unit": {...}, "deleted": number of elements}
    """
    element = get_unit(doc, reference)
    ids = [instance.Id for instance in _unit_instances(element)]
    if ids:
        with Transaction(doc, "PDSO - Clear Unit Layout"):
            doc.Delete(id_list(ids))
    return {"unit": _unit_info(element), "deleted": len(ids)}


def describe_item(doc, element_id):
    """Describe one family instance: position, flips, plan box and instance parameters.

    Returns:
        dict: {"id", "category", "family", "type", "x", "y", "z", "rotation",
            "hand_flipped", "facing_flipped", "mirrored", "can_flip_hand",
            "can_flip_facing", "box" [min_x, min_y, max_x, max_y] in project
            coordinates, "parameters" {name: number}}
    """
    instance = doc.GetElement(to_element_id(str(element_id)))
    if instance is None or not isinstance(instance, DB.FamilyInstance):
        raise PDSOError("Element {} is not a family instance.".format(element_id), 404)
    location = instance.Location
    point = location.Point if isinstance(location, DB.LocationPoint) else None
    box = instance.get_BoundingBox(None)
    return {
        "id": id_value(instance.Id),
        "category": instance.Category.Name,
        "family": instance.Symbol.Family.Name,
        "type": layout_service.get_type_name(instance.Symbol),
        "x": point.X if point else None,
        "y": point.Y if point else None,
        "z": point.Z if point else None,
        "rotation": location.Rotation if point else None,
        "hand_flipped": bool(instance.HandFlipped),
        "facing_flipped": bool(instance.FacingFlipped),
        "mirrored": bool(instance.Mirrored),
        "can_flip_hand": bool(instance.CanFlipHand),
        "can_flip_facing": bool(instance.CanFlipFacing),
        "level_id": id_value(instance.LevelId),
        "level_id_for_unit": id_value(layout_service._instance_level_id(instance)),
        "host": (
            None
            if instance.Host is None
            else "{} {}".format(type(instance.Host).__name__, id_value(instance.Host.Id))
        ),
        "host_face": instance.HostFace is not None,
        "super_component": instance.SuperComponent is not None,
        "box": [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y] if box else None,
        "parameters": layout_service.instance_parameters(instance),
    }


def units_overview(doc, scheme=None):
    """All units with their outline and the plan boxes of their free-standing families.

    One pass over the model, for a unit list with plan thumbnails.

    Args:
        scheme (str): Area scheme of the units. Default: unit_scheme

    Returns:
        list: [{"id", "number", "name", "scheme", "level", "level_id", "area",
            "uid", "outline" [[x, y]], "boxes" [[[x, y] x 4]], "item_count"}],
            sorted by level elevation and Area number
    """
    found, _ = layout_service.units(doc, scheme=scheme or unit_scheme(doc))
    listed = []
    by_level = {}
    for element, silo in found:
        outline = layout_service.get_outline(element)
        if len(outline) < 3:
            continue
        level = layout_service.get_level(element)
        info = _unit_info(element)
        info.update(
            {
                "uid": parameter_access.get_value(silo, parameter_access.PROGRAM_UID),
                "outline": [[x, y] for x, y in outline],
                "boxes": [],
                "item_count": 0,
                "level_id": id_value(element.LevelId),
            }
        )
        xs = [point[0] for point in outline]
        ys = [point[1] for point in outline]
        record = (info, outline, min(xs), min(ys), max(xs), max(ys))
        by_level.setdefault(id_value(element.LevelId), []).append(record)
        listed.append((level.ProjectElevation if level is not None else 0.0, info))

    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    pdso_kind = pdso_kinds(doc)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        location = instance.Location
        if not isinstance(location, DB.LocationPoint) or instance.SuperComponent is not None:
            continue
        if pdso_kind(instance) is not None:
            continue
        host = instance.Host
        if (host is not None and not isinstance(host, DB.Level)) or instance.HostFace is not None:
            continue
        candidates = by_level.get(id_value(layout_service._instance_level_id(instance)))
        if not candidates:
            continue
        x, y = location.Point.X, location.Point.Y
        for info, outline, min_x, min_y, max_x, max_y in candidates:
            if x < min_x or x > max_x or y < min_y or y > max_y:
                continue
            if not point_in_polygon((x, y), outline):
                continue
            info["item_count"] += 1
            box = instance.get_BoundingBox(None)
            if box is not None:
                info["boxes"].append(
                    [
                        [box.Min.X, box.Min.Y],
                        [box.Max.X, box.Min.Y],
                        [box.Max.X, box.Max.Y],
                        [box.Min.X, box.Max.Y],
                    ]
                )
            break
    listed.sort(key=lambda pair: (pair[0], pair[1]["number"] or ""))
    return [info for _, info in listed]


# =============================================================================
# Harvest
# =============================================================================


def suggest_program(unit, fallback):
    """The program of a harvested unit, when none is given.

    The data silo's Unit Layout Type, Program Display Name or Program Type,
    else a dwelling (its data silo counts a bedroom, bathroom or kitchen) is
    named by its bedroom count. Else the fallback (the Area's name), else
    "Unit".
    """
    for key in (
        "PDSO - Unit Layout Type",
        "PDSO - Program Display Name",
        "PDSO - Program Type",
    ):
        value = pdso_value(unit, key)
        # A layout id that Pull from Catalogue wrote is not a program name
        if value and not is_entry_id(str(value).strip()):
            return str(value)
    bedrooms = unit.get("bedrooms", 0)
    if bedrooms or unit.get("bathrooms") or unit.get("kitchens"):
        return "Studio" if bedrooms == 0 else "{} Bedroom".format(bedrooms)
    return fallback or "Unit"


def units_in_view(doc, view):
    """The units of an area plan: its Areas that have a PDSO data silo inside.

    Returns:
        tuple: (areas, without): the Areas with a silo, and the placed Areas
            with none
    """
    if not layout_service.is_area_plan(view):
        raise PDSOError(
            "'{}' is not an area plan. Harvest All Units works on the Areas of "
            "an area plan.".format(view.Name)
        )
    return layout_service.areas_with_data_silo(doc, view)


def harvest_units(
    doc,
    elements,
    project_type="Other",
    view=None,
    name=None,
    program=None,
    notes="",
    folder=None,
    replace_previous=True,
):
    """Save the layout of each unit (an Area) into the catalogue.

    Every harvest goes through here (Harvest Layout, Harvest All Units and the
    API), so their entries are made the same way:
    - program: the one given, else suggest_program;
    - name: the one given (one element only), else
      "<program> - <area> sf - Area <number>";
    - id: unique in the folder;
    - catalogue number: a shape keeps its number in the folder. Numbers are
      given while the folder is locked (catalogue_lock);
    - with replace_previous, an earlier harvest of the same unit is removed,
      after the new entry is saved;
    - the family of each item is saved in the folder's families/ folder, so
      the layout can go into a model that doesn't have it;
    - with a view (a plan), a PNG plan image of each unit is saved beside it.

    Must be called with no open transaction.

    Returns:
        dict: "folder", "units" (one dict per saved unit: element_id,
            element_kind, layout_id, name, program, catalogue_number, file,
            image, family_files, skipped, layout), "failures" ([{"id",
            "reason"}]), "replaced", "images", "image_error". Each unit also
            has "_element", "_entry" and "_result" for the ribbon; public_report
            leaves them out.
    """
    if (name or program) and len(elements) != 1:
        raise PDSOError("A name or a program can be given for one unit only.")
    folder = folder or get_catalogue_folder()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    used_ids = set()
    units, failures, replaced = [], [], 0

    lock = catalogue_lock(folder)
    try:
        lock.__enter__()
    except RuntimeError as error:
        raise PDSOError(str(error), 409)
    try:
        numbers = catalogue_numbers(folder)
        for element in elements:
            lock.touch()
            try:
                result = layout_service.harvest(element, "", "", project_type, notes)
                if result is None:
                    failures.append(
                        {
                            "id": id_value(element.Id),
                            "reason": "The Area is not placed or not enclosed. "
                            "It has no boundary.",
                        }
                    )
                    continue
                entry = result.entry
                features = entry.features
                source = entry.source
                entry.program = program or suggest_program(
                    features.get("unit", {}),
                    layout_service.get_name(element)
                    or layout_service.get_department(element),
                )
                label = (
                    source.get("area_number")
                    or source.get("area_name")
                    or source["element_id"]
                )
                entry.name = name or "{} - {:.0f} sf - {} {}".format(
                    entry.program, entry.area, source["element_kind"], label
                )
                base = "{}--{}--{}".format(
                    slugify(entry.program), slugify(entry.name), stamp
                )
                entry.id, count = base, 1
                while entry.id in used_ids or os.path.exists(
                    os.path.join(folder, entry.id + ENTRY_EXTENSION)
                ):
                    count += 1
                    entry.id = "{}-{}".format(base, count)
                used_ids.add(entry.id)

                number = assign_catalogue(entry, numbers)
                # Save first: a failed save must not leave the unit with no entry
                path = save_entry(entry, folder)
                if replace_previous:
                    replaced += remove_previous(folder, source, keep=path)
                units.append(
                    {
                        "_element": element,
                        "_entry": entry,
                        "_result": result,
                        "file": path,
                        "catalogue_number": number,
                        "image": None,
                    }
                )
            except Exception as error:
                failures.append(
                    {
                        "id": id_value(element.Id),
                        "reason": "{}: {}".format(type(error).__name__, error),
                    }
                )
    finally:
        lock.__exit__(None, None, None)

    # The family files: outside the lock, since each takes a while. A file
    # that is there is kept, so a family is saved once per folder.
    for unit in units:
        try:
            unit["family_files"] = layout_service.save_family_files(
                doc, unit["_entry"], folder
            )
        except Exception as error:
            unit["family_files"] = {"saved": [], "failed": {"(all)": str(error)}}

    # A plan image of each unit, from a temporary copy of the view with tags
    # and Area lines hidden. The copy is undone; the model does not change.
    images, image_error = 0, ""
    if view is not None and units:
        imager = UnitImager(doc, view)
        try:
            for unit in units:
                entry = unit["_entry"]
                outline = [start for start, _, _ in boundary_edges(unit["_element"])]
                image_path = unit["file"][: -len(ENTRY_EXTENSION)] + IMAGE_EXTENSION
                if imager.export(outline, image_path):
                    entry.features["image"] = entry.id + IMAGE_EXTENSION
                    save_entry(entry, folder)
                    unit["image"] = image_path
                    images += 1
        finally:
            image_error = imager.error
            imager.close()

    for unit in units:
        entry, element = unit["_entry"], unit["_element"]
        unit.update(
            {
                "element_id": id_value(element.Id),
                "element_kind": entry.source.get("element_kind"),
                "layout_id": entry.id,
                "name": entry.name,
                "program": entry.program,
                "skipped": [
                    {"id": id_value(element_id), "label": label, "reason": reason}
                    for element_id, label, reason in unit["_result"].skipped
                ],
                "layout": entry_summary(entry),
            }
        )
    return {
        "folder": folder,
        "units": units,
        "failures": failures,
        "replaced": replaced,
        "images": images,
        "image_error": image_error,
    }


def public_report(report):
    """A harvest_units report without its Revit objects, for JSON."""
    report = dict(report)
    report["units"] = [
        dict((key, value) for key, value in unit.items() if not key.startswith("_"))
        for unit in report["units"]
    ]
    return report


def harvest_layout(
    doc, reference, name=None, program=None, project_type="Other", notes="", folder=None
):
    """Save the layout of one unit into the catalogue (see harvest_units).

    Each call makes a new layout; it doesn't replace an earlier harvest of the
    same unit.

    Args:
        doc: Revit Document
        reference: Id of the unit's Area or data silo, or its Program UID
        name (str): Name of the layout. Default: "<program> - <area> sf - ..."
        program (str): Program that the layout serves. Default: suggest_program
        project_type (str): For example "Residential" or "K-12"
        notes (str): Free text
        folder (str): Folder for the files. Default: the configured folder

    Returns:
        dict: {"layout": summary, "file": path of the JSON file, "entry": the
            stored JSON, "skipped": [{"id", "label", "reason"}],
            "catalogue_number", "family_files"}
    """
    element = get_unit(doc, reference)
    report = harvest_units(
        doc,
        [element],
        project_type,
        name=name,
        program=program,
        notes=notes,
        folder=folder,
        replace_previous=False,
    )
    if report["failures"]:
        raise PDSOError(report["failures"][0]["reason"], 409)
    unit = report["units"][0]
    return {
        "layout": unit["layout"],
        "file": unit["file"],
        "entry": unit["_entry"].to_dict(),
        "skipped": unit["skipped"],
        "catalogue_number": unit["catalogue_number"],
        "family_files": unit["family_files"],
    }


# =============================================================================
# Recommend and place
# =============================================================================


def _placement_info(placement):
    return {
        "turns": placement.turns,
        "mirror": placement.mirror,
        "description": placement.describe(),
    }


def recommend_layouts(doc, reference, program=None, limit=10):
    """Rank the catalogue layouts for a target unit.

    Args:
        doc: Revit Document
        reference: Id of the unit's Area or data silo, or its Program UID
        program (str): Layouts for this program come first. Default: the Area name
        limit (int): Largest number of layouts in the reply

    Returns:
        dict: {"target": {...}, "recommendations": [{"layout_id", "name",
            "program", "score", "area_score", "snug_score", "program_match",
            "shape_match", "placement"}], "rejected": [{"layout_id", "reason"}]}
    """
    element = get_unit(doc, reference)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Area is not placed or not enclosed. It has no boundary.", 409
        )
    if program is None:
        program = layout_service.get_name(element)

    entries, errors = _entries()
    recommendations, rejected = recommend(
        entries, frame.width, frame.depth, area, local_outline, program
    )
    target = _unit_info(element)
    target.update({"width": frame.width, "depth": frame.depth, "program": program})
    return {
        "target": target,
        "recommendations": [
            {
                "layout_id": item.entry.id,
                "name": item.entry.name,
                "program": item.entry.program,
                "score": item.score,
                "area_score": item.area_score,
                "snug_score": item.snug_score,
                "program_match": item.program_match,
                "shape_match": shape_match(item.entry, frame, local_outline)[0],
                "placement": _placement_info(item.placement),
            }
            for item in recommendations[: int(limit)]
        ],
        "rejected": [
            {"layout_id": entry.id, "reason": reason} for entry, reason in rejected
        ],
        "errors": errors,
    }


def place_layout(doc, reference, entry_id, clear_first=False):
    """Place a catalogue layout into a target unit.

    The recommender selects the orientation. If the layout fits in no
    orientation, nothing is placed.

    Args:
        clear_first (bool): Delete the free-standing families in the target
            first (as clear_unit_items does). The delete and the placement
            are one transaction: if the placement fails, nothing is deleted.

    Returns:
        dict: {"target": {...}, "layout_id", "placement", "placed": [element
            ids], "missing": {"Family : Type": count}, "loaded_families":
            {family name: where it came from}, "tagged_programs":
            [element ids], "deleted": number of elements deleted first,
            "warnings": [str]}
    """
    element = get_unit(doc, reference)
    entry = get_entry(entry_id)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Area is not placed or not enclosed. It has no boundary.", 409
        )
    recommendations, rejected = recommend(
        [entry], frame.width, frame.depth, area, local_outline, entry.program
    )
    if not recommendations:
        raise PDSOError(
            "Layout '{}' can't be placed in this target: {}".format(entry_id, rejected[0][1]),
            409,
        )
    chosen = recommendations[0]
    placement = refine_placement(
        entry, chosen.placement, frame.width, frame.depth, local_outline
    )
    # Families the model hasn't got are loaded first (their own undo step):
    # from the family files of the catalogue, or from another open model
    families = layout_service.load_missing_families(doc, entry, catalogue_folders())
    deleted = 0
    with Transaction(doc, "PDSO - Place Unit Layout") as transaction:
        if clear_first:
            ids = [instance.Id for instance in _unit_instances(element)]
            if ids:
                doc.Delete(id_list(ids))
            deleted = len(ids)
        result = layout_service.create(element, entry, placement)
        # create() skips items whose family is not loaded. If it placed
        # nothing, the unit must not stay emptied: roll everything back.
        if clear_first and deleted and not result.placed:
            reasons = list(result.warnings)
            if result.missing:
                reasons.append(
                    "Families not loaded in this model: {}".format(
                        ", ".join(sorted(result.missing))
                    )
                )
            raise PDSOError(
                "No item of layout '{}' could be placed, so the furniture in the "
                "unit was not deleted. {}".format(entry_id, " ".join(reasons)).strip(),
                409,
            )
    return {
        "deleted": deleted,
        "target": _unit_info(element),
        "layout_id": entry.id,
        "score": chosen.score,
        "placement": _placement_info(placement),
        "placed": [id_value(element_id) for element_id in result.placed],
        "missing": result.missing,
        "loaded_families": families["loaded"],
        "tagged_programs": [id_value(element_id) for element_id in result.tagged_silos],
        "warnings": list(result.warnings) + list(transaction.warnings),
    }


# =============================================================================
# Similar units
# =============================================================================


def shape_match(entry, frame, local_outline):
    """How well the outline of an entry covers the outline of a target.

    The entry outline is put in the middle of the target frame in each of the
    eight orientations. The result is the largest intersection over union.

    Args:
        entry (LayoutEntry): The layout
        frame (UnitFrame): Frame of the target
        local_outline (list): Target outline in its frame

    Returns:
        tuple: (match from 0.0 to 1.0, (turns, mirror) of the best orientation)
    """
    if not entry.boundary or not local_outline:
        return 0.0, (0, False)
    best = 0.0
    best_orientation = (0, False)
    for turns, mirror in ORIENTATIONS:
        width, depth = orient_size(entry.width, entry.depth, turns)
        shift_x = (frame.width - width) / 2.0
        shift_y = (frame.depth - depth) / 2.0
        outline = []
        for point in entry.boundary:
            x, y = orient_point(point, entry.width, entry.depth, turns, mirror)
            outline.append((x + shift_x, y + shift_y))
        match = outline_overlap(outline, local_outline, SHAPE_GRID)
        if match > best:
            best = match
            best_orientation = (turns, mirror)
    return best, best_orientation


def area_match(entry, area):
    """How well the area of a target agrees with the area of an entry (0 to 1)."""
    if entry.area <= 0:
        return 0.0
    return max(0.0, 1.0 - abs(area - entry.area) / entry.area)


def find_similar_units(doc, entry_id, scheme=None, level=None, limit=20):
    """Find the units of a model that are similar to a layout.

    Each unit gets these numbers:
        similarity: the average of area_match and shape_match (0.0 to 1.0). It
            does not depend on the items of the layout
        area_match: how well the two areas agree
        shape_match: how well the two outlines cover each other
        fits: True if all items of the layout land in the unit in an orientation
        score: the recommender's score if the layout fits, else 0.0

    Args:
        doc: Revit Document
        entry_id (str): Id of the layout
        scheme (str): Area scheme of the units. Default: unit_scheme
        level: Level name or id (optional)
        limit (int): Largest number of units in the reply

    Returns:
        dict: {"layout": summary, "units_checked": int, "units": [{"id",
            "number", "name", "scheme", "level", "area", "similarity",
            "area_match", "shape_match", "fits", "score", "placement",
            "reason"}]} ranked by similarity
    """
    entry = get_entry(entry_id)
    source_id = str(entry.source.get("element_id", ""))
    source_project = entry.source.get("project")
    level_element = find_level(doc, level) if level is not None else None
    found, _ = layout_service.units(
        doc,
        scheme=scheme or unit_scheme(doc),
        level_id=id_value(level_element.Id) if level_element is not None else None,
    )

    similar = []
    for element, _silo in found:
        frame, local_outline, area = layout_service.describe_target(element)
        if frame is None:
            continue
        info = _unit_info(element)
        info["is_source"] = (
            str(info["id"]) == source_id and doc.Title == source_project
        )
        recommendations, rejected = recommend(
            [entry], frame.width, frame.depth, area, local_outline, ""
        )
        shape, _ = shape_match(entry, frame, local_outline)
        area_score = area_match(entry, area)
        info.update(
            {
                "similarity": (area_score + shape) / 2.0,
                "area_match": area_score,
                "shape_match": shape,
                "fits": bool(recommendations),
                "score": recommendations[0].score if recommendations else 0.0,
                "placement": (
                    _placement_info(recommendations[0].placement)
                    if recommendations
                    else None
                ),
                "reason": None if recommendations else rejected[0][1],
            }
        )
        similar.append(info)

    similar.sort(key=lambda item: (-item["similarity"], -item["shape_match"]))
    return {
        "layout": entry_summary(entry),
        "units_checked": len(similar),
        "units": similar[: int(limit)],
    }


# =============================================================================
# Preview (read-only)
# =============================================================================

# An angle this near to a multiple of 90 degrees counts as square (radians)
SQUARE_ANGLE_TOLERANCE = 1e-3


def _is_square(angle):
    rest = angle % (math.pi / 2.0)
    return min(rest, math.pi / 2.0 - rest) < SQUARE_ANGLE_TOLERANCE


def _own_box(instance):
    """Plan box of an instance in the frame of its location point and rotation.

    The box comes from the bounding box of the instance, which is parallel to
    the project axes. Thus it is exact only if the rotation is square.

    Returns:
        tuple: (min_x, min_y, max_x, max_y), or None
    """
    box = instance.get_BoundingBox(None)
    if box is None:
        return None
    point = instance.Location.Point
    angle = instance.Location.Rotation
    corners = [
        rotate((x - point.X, y - point.Y), -angle)
        for x, y in (
            (box.Min.X, box.Min.Y),
            (box.Max.X, box.Min.Y),
            (box.Max.X, box.Max.Y),
            (box.Min.X, box.Max.Y),
        )
    ]
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_corners(box, x, y, angle):
    """The four corners of a box in project coordinates: [[x, y], ...]."""
    corners = []
    for local in (
        (box[0], box[1]),
        (box[2], box[1]),
        (box[2], box[3]),
        (box[0], box[3]),
    ):
        dx, dy = rotate(local, angle)
        corners.append([x + dx, y + dy])
    return corners


def _type_boxes(doc, keys):
    """Plan boxes of family types, measured on instances that are in the model.

    Args:
        doc: Revit Document
        keys (set): (family name, type name) pairs

    Returns:
        dict: key -> list of (exact, hand_flipped, facing_flipped, box,
            can_flip_hand, can_flip_facing)
    """
    boxes = {}
    if not keys:
        return boxes
    categories = layout_service.List[DB.BuiltInCategory]()
    for category in layout_service.HARVEST_CATEGORIES:
        categories.Add(category)
    for instance in (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    ):
        if not isinstance(instance.Location, DB.LocationPoint):
            continue
        try:
            key = (
                instance.Symbol.Family.Name,
                layout_service.get_type_name(instance.Symbol),
            )
        except Exception:
            continue
        if key not in keys:
            continue
        known = boxes.setdefault(key, [])
        exact = _is_square(instance.Location.Rotation)
        hand = bool(instance.HandFlipped)
        facing = bool(instance.FacingFlipped)
        if any(k[0] >= exact and k[1] == hand and k[2] == facing for k in known):
            continue
        box = _own_box(instance)
        if box is not None:
            known.append(
                (
                    exact,
                    hand,
                    facing,
                    box,
                    bool(instance.CanFlipHand),
                    bool(instance.CanFlipFacing),
                )
            )
    return boxes


def _box_for(known, hand, facing):
    """Select the box of a family type for an item with the given flips."""
    if not known:
        return None

    def rank(candidate):
        return (not candidate[0], candidate[1] != hand, candidate[2] != facing)

    _, known_hand, known_facing, box = sorted(known, key=rank)[0][:4]
    min_x, min_y, max_x, max_y = box
    if known_hand != hand:
        min_x, max_x = -max_x, -min_x
    if known_facing != facing:
        min_y, max_y = -max_y, -min_y
    return (min_x, min_y, max_x, max_y)


def _current_items(element):
    """The free-standing items in a unit now, for a preview."""
    current = []
    for instance in _unit_instances(element):
        point = instance.Location.Point
        angle = instance.Location.Rotation
        box = _own_box(instance)
        current.append(
            {
                "id": id_value(instance.Id),
                "category": instance.Category.Name,
                "family": instance.Symbol.Family.Name,
                "type": layout_service.get_type_name(instance.Symbol),
                "x": point.X,
                "y": point.Y,
                "rotation": angle,
                "hand_flipped": bool(instance.HandFlipped),
                "facing_flipped": bool(instance.FacingFlipped),
                "mirrored": bool(instance.Mirrored),
                "can_flip_hand": bool(instance.CanFlipHand),
                "can_flip_facing": bool(instance.CanFlipFacing),
                "footprint": _box_corners(box, point.X, point.Y, angle) if box else None,
            }
        )
    current.sort(key=lambda item: (item["family"], item["type"], item["x"], item["y"]))
    return current


def preview_layout_in_unit(doc, reference, entry_id, lookups=None):
    """Data for a picture of a layout in a unit. The model does not change.

    The items of the layout get the positions and rotations that place_layout
    would use. If the layout does not fit, the orientation with the best
    outline match is used, and each item says if it is inside the unit.
    All points are in project coordinates.

    lookups (dict, optional): pass the same dict to several calls on a model
    that doesn't change between them (the Pull window's rows). The family
    types, their plan boxes and each unit's current items are then collected
    once, not once per call.

    Returns:
        dict: {"unit": {..., "outline", "width", "depth", "frame_angle"},
            "layout": summary, "match": {"similarity", "area_match",
            "shape_match", "fits", "score", "reason", "placement",
            "orientation_from", "items_outside", "items_not_loaded"},
            "layout_outline": [[x, y], ...], "items": [{"category", "family",
            "type", "x", "y", "rotation", "inside", "loaded", "footprint"}],
            "current_items": [{"id", "category", "family", "type", "x", "y",
            "rotation", "footprint"}]}
    """
    element = get_unit(doc, reference)
    entry = get_entry(entry_id)
    outline = layout_service.get_outline(element)
    frame, local_outline, area = layout_service.describe_target(element)
    if frame is None:
        raise PDSOError(
            "The Area is not placed or not enclosed. It has no boundary.", 409
        )

    recommendations, rejected = recommend(
        [entry], frame.width, frame.depth, area, local_outline, ""
    )
    shape, shape_orientation = shape_match(entry, frame, local_outline)
    area_score = area_match(entry, area)
    if recommendations:
        placement = refine_placement(
            entry, recommendations[0].placement, frame.width, frame.depth, local_outline
        )
        orientation_from = "placement"
    else:
        turns, mirror = shape_orientation
        width, depth = orient_size(entry.width, entry.depth, turns)
        shift = ((frame.width - width) / 2.0, (frame.depth - depth) / 2.0)
        placement = Placement(turns, mirror, shift)
        orientation_from = "best outline match"

    lookups = {} if lookups is None else lookups
    if "symbols" not in lookups:
        lookups["symbols"] = layout_service._symbol_lookup(doc)
    symbols = lookups["symbols"]
    # Families that place_layout would load first count as loaded
    loadable = layout_service.loadable_families(doc, entry, catalogue_folders())
    to_load = 0
    boxes = lookups.setdefault("boxes", {})
    missing = set(item.symbol_key for item in entry.items) - set(boxes)
    if missing:
        found = _type_boxes(doc, missing)
        for key in missing:
            boxes[key] = found.get(key)
    items = []
    outside = 0
    not_loaded = 0
    across = 0
    for item, local_point in oriented_items(entry, placement):
        x, y = frame.to_world(local_point)
        angle = frame.angle + orient_angle(
            item.rotation, placement.turns, placement.mirror
        )
        inside = point_in_or_near_polygon(local_point, local_outline, FIT_TOLERANCE)
        loaded = item.symbol_key in symbols
        if not loaded and item.family in loadable:
            loaded = True
            to_load += 1
        outside += 0 if inside else 1
        not_loaded += 0 if loaded else 1
        known = boxes.get(item.symbol_key)
        hand = item.hand_flipped != placement.mirror
        facing = item.facing_flipped
        box = _box_for(known, hand, facing)
        footprint = _box_corners(box, x, y, angle) if box else None
        # place_layout checks the insertion point only. The body of an item
        # can be in a wall: a corner of its plan box outside the unit outline.
        is_across = bool(
            inside
            and footprint
            and any(
                not point_in_polygon((corner[0], corner[1]), outline)
                and distance_to_polygon((corner[0], corner[1]), outline) > FIT_TOLERANCE
                for corner in footprint
            )
        )
        across += 1 if is_across else 0
        items.append(
            {
                "category": item.category,
                "family": item.family,
                "type": item.type,
                "x": x,
                "y": y,
                "rotation": angle % (2.0 * math.pi),
                "hand_flipped": hand,
                "facing_flipped": facing,
                "inside": inside,
                "loaded": loaded,
                "across": is_across,
                "footprint": footprint,
            }
        )

    current_by_unit = lookups.setdefault("current", {})
    current = current_by_unit.get(id_value(element.Id))
    if current is None:
        current = current_by_unit[id_value(element.Id)] = _current_items(element)

    unit = _unit_info(element)
    unit.update(
        {
            "outline": [[x, y] for x, y in outline],
            "width": frame.width,
            "depth": frame.depth,
            "frame_angle": frame.angle,
            "is_source": (
                str(unit["id"]) == str(entry.source.get("element_id", ""))
                and doc.Title == entry.source.get("project")
            ),
        }
    )
    return {
        "unit": unit,
        "layout": entry_summary(entry),
        "match": {
            "similarity": (area_score + shape) / 2.0,
            "area_match": area_score,
            "shape_match": shape,
            "fits": bool(recommendations),
            "score": recommendations[0].score if recommendations else 0.0,
            "reason": None if recommendations else rejected[0][1],
            "placement": _placement_info(placement),
            "orientation_from": orientation_from,
            "items_outside": outside,
            "items_across_outline": across,
            "items_not_loaded": not_loaded,
            "items_to_load": to_load,
            "families_to_load": sorted(loadable),
        },
        "layout_outline": [
            list(frame.to_world(point))
            for point in oriented_outline(entry, placement)
        ],
        "items": items,
        "current_items": current,
    }
