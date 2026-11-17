# -*- coding: utf-8 -*-
"""Harvest and Create steps: the Revit side of the Unit Layout Catalogue.

Harvest reads a unit (an Area with a PDSO data silo inside) and the families
standing in it, and turns them into a LayoutEntry in the unit's own frame.
Create does the reverse: it places an entry's families into a target unit
through a Placement chosen by
the recommender.

Harvest also reads what surrounds the layout (which walls are exterior or face
the corridor, the doors and windows, the unit's shape); see harvest_features.

Limits, reported to the user rather than hidden:
    - Only free-standing (level-based) families are placed by Create. Wall- and
      face-hosted families (wall cabinets, whiteboards) are recorded in the
      entry's features, with the wall they sit on, but not placed yet.
    - A family that is not loaded in the target model is listed as missing.
    - Only a unit's outer boundary is used; holes (columns, shafts) are ignored.
"""

import os
from datetime import datetime

from rpw import DB
from rpw.utils.dotnet import List

from pdso.code.catalogue.harvest_features import (
    DATA_SILO,
    pdso_kinds,
    boundary_edges,
    build_features,
    id_value,
    nearest_edge,
    type_name,
)
from pdso.code.catalogue.layout_entry import LayoutEntry, LayoutItem, is_entry_id
from pdso.code.catalogue.layout_geometry import (
    UnitFrame,
    orient_angle,
    point_in_polygon,
    polygon_area,
)
from pdso.code.catalogue.layout_recommender import oriented_items
from pdso.code.setup.revit_utils import FamilyLoadOptions

# Categories a layout is made of
HARVEST_CATEGORIES = [
    DB.BuiltInCategory.OST_Furniture,
    DB.BuiltInCategory.OST_FurnitureSystems,
    DB.BuiltInCategory.OST_Casework,
    DB.BuiltInCategory.OST_SpecialityEquipment,
    DB.BuiltInCategory.OST_PlumbingFixtures,
    DB.BuiltInCategory.OST_ElectricalEquipment,
    DB.BuiltInCategory.OST_GenericModel,
]

# The PDSO parameter the catalogue key is written to
UNIT_LAYOUT_PARAMETER = "PDSO - Unit Layout Type"


# ---------------------------------------------------------------------------
# Reading a unit's Area
# ---------------------------------------------------------------------------


def is_unit_area(element):
    """True for an Area. PDSO units are Areas; Revit Rooms are never used."""
    return isinstance(element, DB.Area)


def get_outline(spatial_element):
    """The outer boundary of an Area as [(x, y), ...] in project feet.

    It follows the Area's boundary rules (wall centrelines or area
    boundary lines).

    Returns:
        list: Points, or [] if the element is unplaced or not enclosed.
    """
    if spatial_element.Location is None or spatial_element.Area <= 0:
        return []

    options = DB.SpatialElementBoundaryOptions()
    loops = spatial_element.GetBoundarySegments(options)
    if not loops or loops.Count == 0:
        return []

    # Revit returns the outer loop first
    points = []
    for segment in loops[0]:
        tessellated = list(segment.GetCurve().Tessellate())
        # Each curve's last point is the next curve's first, so drop it
        for xyz in tessellated[:-1]:
            points.append((xyz.X, xyz.Y))
    return points


def get_level(spatial_element):
    doc = spatial_element.Document
    return doc.GetElement(spatial_element.LevelId)


def get_text_parameter(element, built_in_parameter):
    parameter = element.get_Parameter(built_in_parameter)
    if parameter is None or not parameter.HasValue:
        return ""
    return parameter.AsString() or ""


def get_name(spatial_element):
    return get_text_parameter(spatial_element, DB.BuiltInParameter.ROOM_NAME)


def get_department(spatial_element):
    return get_text_parameter(spatial_element, DB.BuiltInParameter.ROOM_DEPARTMENT)


def describe_target(spatial_element):
    """Frame, outline in that frame, and area of a target Area.

    Returns:
        tuple: (frame, local_outline, area), or (None, None, None) when the
            element has no usable boundary.
    """
    outline = get_outline(spatial_element)
    if len(outline) < 3:
        return None, None, None
    frame = UnitFrame.from_polygon(outline)
    local_outline = [frame.to_local(p) for p in outline]
    return frame, local_outline, spatial_element.Area


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------


def _model_id(doc):
    """A stable id of the model: the UniqueId of its Project Information."""
    try:
        return doc.ProjectInformation.UniqueId
    except Exception:
        return ""


def is_area_plan(view):
    return isinstance(view, DB.ViewPlan) and view.ViewType == DB.ViewType.AreaPlan


def data_silos(doc):
    """The placed PDSO data silos: [(level id, x, y, instance)]."""
    silos = []
    kind = pdso_kinds(doc)
    for instance in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        if kind(instance) != DATA_SILO:
            continue
        location = instance.Location
        if isinstance(location, DB.LocationPoint):
            silos.append(
                (id_value(instance.LevelId), location.Point.X, location.Point.Y, instance)
            )
    return silos


def units(doc, view=None, scheme=None, level_id=None):
    """The units: placed Areas that have a PDSO data silo inside.

    Args:
        view: Only the Areas shown in this view (an area plan)
        scheme (str): Only the Areas of this area scheme
        level_id (int): Only the Areas on this level

    Returns:
        tuple: (units, without). units is [(Area, data silo instance)];
            without is the placed Areas with no silo.
    """
    silos = data_silos(doc)
    collector = (
        DB.FilteredElementCollector(doc, view.Id)
        if view is not None
        else DB.FilteredElementCollector(doc)
    )
    found, without = [], []
    for area in collector.OfCategory(DB.BuiltInCategory.OST_Areas).WhereElementIsNotElementType():
        if not isinstance(area, DB.Area) or area.Area <= 0:
            continue
        if scheme is not None and _scheme_name(area) != scheme:
            continue
        if level_id is not None and id_value(area.LevelId) != level_id:
            continue
        outline = [start for start, _, _ in boundary_edges(area)]
        if len(outline) < 3:
            continue
        inside = [
            silo
            for silo_level, x, y, silo in silos
            if silo_level == id_value(area.LevelId) and point_in_polygon((x, y), outline)
        ]
        if inside:
            found.append((area, inside[0]))
        else:
            without.append(area)
    return found, without


def _scheme_name(area):
    try:
        return area.AreaScheme.Name
    except Exception:
        return None


def areas_with_data_silo(doc, view):
    """The placed Areas shown in a view that have a PDSO data silo inside.

    Returns:
        tuple: (areas, without). without is the placed Areas with no silo.
    """
    found, without = units(doc, view=view)
    return [area for area, _ in found], without


class HarvestResult(object):
    """A harvested entry plus what was left out and why."""

    def __init__(self, entry, skipped):
        self.entry = entry
        self.skipped = skipped  # list of (element id, "Family : Type", reason)


# Kept under its old name for callers outside this module
get_type_name = type_name


def _label(instance):
    try:
        return "{} : {}".format(
            instance.Symbol.Family.Name, get_type_name(instance.Symbol)
        )
    except Exception:
        return "element {}".format(instance.Id)


def _instance_level_id(instance):
    if instance.LevelId != DB.ElementId.InvalidElementId:
        return instance.LevelId
    if isinstance(instance.Host, DB.Level):
        return instance.Host.Id
    # An instance that was mirrored in place can have no level id and no host.
    # Its level is then in one of these parameters.
    for built_in in (
        DB.BuiltInParameter.FAMILY_LEVEL_PARAM,
        DB.BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM,
        DB.BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
    ):
        parameter = instance.get_Parameter(built_in)
        if parameter is not None and parameter.HasValue:
            level_id = parameter.AsElementId()
            if level_id != DB.ElementId.InvalidElementId:
                return level_id
    return DB.ElementId.InvalidElementId


def _z_offset(instance, point, level):
    parameter = instance.get_Parameter(DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM)
    if parameter is not None and parameter.HasValue:
        return parameter.AsDouble()
    return point.Z - level.ProjectElevation


def instance_parameters(instance):
    """The instance parameters that the family defines, as name -> number.

    These give an instance its own size, for example the length of a counter
    top or the position of a sink hole. Built-in parameters (level, height,
    phase), shared parameters, text and element references are not included.

    Returns:
        dict: {parameter name: float (internal units) or int}
    """
    values = {}
    for parameter in instance.Parameters:
        try:
            definition = parameter.Definition
            if definition is None or parameter.IsReadOnly or not parameter.HasValue:
                continue
            if parameter.IsShared:
                continue
            if definition.BuiltInParameter != DB.BuiltInParameter.INVALID:
                continue
            if parameter.StorageType == DB.StorageType.Double:
                values[definition.Name] = round(parameter.AsDouble(), 9)
            elif parameter.StorageType == DB.StorageType.Integer:
                values[definition.Name] = int(parameter.AsInteger())
        except Exception:
            continue
    return values


def apply_instance_parameters(instance, values):
    """Set stored instance parameters on a new instance.

    Returns:
        list: Names of the parameters that could not be set.
    """
    failed = []
    for name in sorted(values):
        value = values[name]
        parameter = instance.LookupParameter(name)
        if parameter is None or parameter.IsReadOnly:
            failed.append(name)
            continue
        try:
            if parameter.StorageType == DB.StorageType.Double:
                if abs(parameter.AsDouble() - float(value)) > 1e-9:
                    parameter.Set(float(value))
            elif parameter.StorageType == DB.StorageType.Integer:
                if parameter.AsInteger() != int(value):
                    parameter.Set(int(value))
            else:
                failed.append(name)
        except Exception:
            failed.append(name)
    return failed


def harvest(spatial_element, name, program, project_type, notes=""):
    """Turn a unit's Area and the families in it into a catalogue entry.

    Returns:
        HarvestResult, or None if the element has no usable boundary.
    """
    doc = spatial_element.Document
    edges = boundary_edges(spatial_element)
    outline = [start for start, _, _ in edges]
    if len(outline) < 3:
        return None

    level = get_level(spatial_element)
    frame = UnitFrame.from_polygon(outline)
    features = build_features(spatial_element, edges, frame, level)

    categories = List[DB.BuiltInCategory]()
    for category in HARVEST_CATEGORIES:
        categories.Add(category)

    candidates = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FamilyInstance)
        .WherePasses(DB.ElementMulticategoryFilter(categories))
        .WhereElementIsNotElementType()
    )

    items, skipped, hosted_items, nested_items = [], [], [], []
    pdso_kind = pdso_kinds(doc)
    for instance in candidates:
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        point = location.Point
        if not point_in_polygon((point.X, point.Y), outline):
            continue
        if _instance_level_id(instance) != level.Id:
            continue

        # A PDSO data silo is the unit's label and a Program Volume is its
        # generated copy. They are not part of the layout.
        if pdso_kind(instance) is not None:
            continue

        local_x, local_y = frame.to_local((point.X, point.Y))

        # Parts of a nested family come along with their parent when it is
        # placed, so they are not items. They are still recorded: a whole
        # furniture layout is often one family, and its desks and chairs are
        # what the layout is made of. (Only shared nested families are seen.)
        if instance.SuperComponent is not None:
            parent = instance.SuperComponent
            while getattr(parent, "SuperComponent", None) is not None:
                parent = parent.SuperComponent
            nested_items.append(
                {
                    "category": instance.Category.Name,
                    "family": instance.Symbol.Family.Name,
                    "type": get_type_name(instance.Symbol),
                    "x": round(local_x, 4),
                    "y": round(local_y, 4),
                    "z_offset": round(_z_offset(instance, point, level), 4),
                    "rotation": round(location.Rotation - frame.angle, 6),
                    "parent_family": parent.Symbol.Family.Name,
                    "parent_type": get_type_name(parent.Symbol),
                }
            )
            continue

        # Hosted families are recorded with the wall they sit on, not placed
        host = instance.Host
        host_name = None
        if host is not None and not isinstance(host, DB.Level):
            host_name = host.Category.Name if host.Category else "an element"
        elif instance.HostFace is not None:
            host_name = "a face"
        if host_name is not None:
            edge_index, edge_distance = nearest_edge((point.X, point.Y), edges)
            hosted_items.append(
                {
                    "category": instance.Category.Name,
                    "family": instance.Symbol.Family.Name,
                    "type": get_type_name(instance.Symbol),
                    "x": round(local_x, 4),
                    "y": round(local_y, 4),
                    "z_offset": round(_z_offset(instance, point, level), 4),
                    "rotation": round(location.Rotation - frame.angle, 6),
                    "host": host_name,
                    "edge": edge_index,
                    "edge_distance": round(edge_distance, 4),
                }
            )
            skipped.append((instance.Id, _label(instance), "hosted by " + host_name))
            continue

        items.append(
            LayoutItem(
                instance.Category.Name,
                instance.Symbol.Family.Name,
                get_type_name(instance.Symbol),
                local_x,
                local_y,
                _z_offset(instance, point, level),
                location.Rotation - frame.angle,
                bool(instance.HandFlipped),
                bool(instance.FacingFlipped),
                bool(instance.Mirrored),
                instance_parameters(instance),
            )
        )

    features["hosted_items"] = hosted_items
    features["nested_items"] = nested_items

    local_outline = [frame.to_local(p) for p in outline]
    # Store the outline counter-clockwise so every entry reads the same way
    if polygon_area(local_outline) < 0:
        local_outline.reverse()

    source = {
        "model": doc.Title,
        # The file's name only: a full path carries the user's name and folders
        "model_path": os.path.basename(doc.PathName or ""),
        # Identifies the model (copies made with Save As keep it). Used to
        # tell the same unit of the same model from another project's.
        "model_id": _model_id(doc),
        "project": doc.Title,
        "element_id": str(id_value(spatial_element.Id)),
        "element_kind": "Area",
        # Revit keeps an Area's name and number in its ROOM_* parameters
        "area_name": get_name(spatial_element),
        "area_number": get_text_parameter(spatial_element, DB.BuiltInParameter.ROOM_NUMBER),
        "department": get_department(spatial_element),
        "level": level.Name,
        "harvested": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    entry = LayoutEntry(
        name,
        program,
        project_type,
        frame.width,
        frame.depth,
        spatial_element.Area,
        local_outline,
        items,
        source,
        notes,
        features=features,
    )
    return HarvestResult(entry, skipped)


# ---------------------------------------------------------------------------
# Family files: a layout can go into a model that does not have its families
# ---------------------------------------------------------------------------

FAMILY_FOLDER = "families"
FAMILY_EXTENSION = ".rfa"


def family_file_name(family_name):
    """File name of a family in the families folder of a catalogue folder."""
    safe = "".join(
        character if character not in '\\/:*?"<>|' else "_" for character in family_name
    )
    return safe.strip() + FAMILY_EXTENSION


def _families_by_name(doc):
    families = {}
    for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        families[family.Name] = family
    return families


def save_family_files(doc, entry, folder):
    """Save the family of each layout item as a file beside the layout.

    The files go to <folder>/families/<family name>.rfa. A file that is there
    is kept. Must be called outside a transaction.

    Returns:
        dict: {"saved": [family names], "failed": {family name: reason}}
    """
    saved, failed = [], {}
    names = sorted(set(item.family for item in entry.items))
    if not names:
        return {"saved": saved, "failed": failed}
    target = os.path.join(folder, FAMILY_FOLDER)
    if not os.path.isdir(target):
        os.makedirs(target)
    families = _families_by_name(doc)
    for name in names:
        path = os.path.join(target, family_file_name(name))
        if os.path.exists(path):
            continue
        family = families.get(name)
        if family is None or not family.IsEditable or family.IsInPlace:
            failed[name] = "the family cannot be saved as a file"
            continue
        family_doc = None
        try:
            family_doc = doc.EditFamily(family)
            options = DB.SaveAsOptions()
            options.OverwriteExistingFile = True
            family_doc.SaveAs(path, options)
            saved.append(name)
        except Exception as error:
            failed[name] = str(error)
        finally:
            if family_doc is not None:
                family_doc.Close(False)
    return {"saved": saved, "failed": failed}


def loadable_families(doc, entry, folders):
    """The families of a layout that the model has not, but that can be loaded.

    This is a read-only forecast of load_missing_families.

    Returns:
        set: Family names
    """
    symbols = _symbol_lookup(doc)
    present = _families_by_name(doc)
    names = set(
        item.family
        for item in entry.items
        if item.symbol_key not in symbols and item.family not in present
    )
    found = set()
    if not names:
        return found
    for name in names:
        for folder in folders:
            if os.path.exists(os.path.join(folder, FAMILY_FOLDER, family_file_name(name))):
                found.add(name)
    for other in doc.Application.Documents:
        if len(found) == len(names):
            break
        if other.IsFamilyDocument or other.Equals(doc):
            continue
        for name, family in _families_by_name(other).items():
            if name in names and family.IsEditable and not family.IsInPlace:
                found.add(name)
    return found


def load_missing_families(doc, entry, folders):
    """Load the families of a layout that the model does not have.

    A family comes from the families folder of a catalogue folder, or from a
    different open model that has it. Must be called outside a transaction.

    Returns:
        dict: {"loaded": {family name: where it came from},
            "not_found": [family names]}
    """
    loaded, not_found = {}, []
    symbols = _symbol_lookup(doc)
    names = sorted(
        set(item.family for item in entry.items if item.symbol_key not in symbols)
    )
    if not names:
        return {"loaded": loaded, "not_found": not_found}
    present = _families_by_name(doc)
    options = FamilyLoadOptions(keep_existing=True)

    for name in names:
        if name in present:
            # The family is loaded, but not this type. A file or a different
            # model cannot add a type to it without a change of the family.
            not_found.append(name)
            continue

        path = None
        for folder in folders:
            candidate = os.path.join(folder, FAMILY_FOLDER, family_file_name(name))
            if os.path.exists(candidate):
                path = candidate
        if path is not None:
            try:
                transaction = DB.Transaction(doc, "PDSO - Load Layout Family")
                transaction.Start()
                try:
                    done = doc.LoadFamily(path, options)
                    transaction.Commit()
                except Exception:
                    transaction.RollBack()
                    raise
                if done or name in _families_by_name(doc):
                    loaded[name] = "file " + os.path.basename(path)
                    continue
            except Exception:
                pass

        source_title = None
        for other in doc.Application.Documents:
            if other.IsFamilyDocument or other.Equals(doc):
                continue
            family = _families_by_name(other).get(name)
            if family is None or not family.IsEditable or family.IsInPlace:
                continue
            family_doc = None
            try:
                family_doc = other.EditFamily(family)
                family_doc.LoadFamily(doc, options)
                source_title = other.Title
            except Exception:
                source_title = None
            finally:
                if family_doc is not None:
                    family_doc.Close(False)
            if source_title is not None:
                break
        if source_title is not None and name in _families_by_name(doc):
            loaded[name] = "open model " + source_title
        else:
            not_found.append(name)
    return {"loaded": loaded, "not_found": not_found}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class CreateResult(object):
    """What placing an entry did."""

    def __init__(self):
        self.placed = []  # ElementIds
        self.missing = {}  # "Family : Type" -> count
        self.warnings = []  # str
        self.tagged_silos = []  # ElementIds of PDSO data silos updated


def _symbol_lookup(doc):
    lookup = {}
    for symbol in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol):
        try:
            lookup[(symbol.Family.Name, get_type_name(symbol))] = symbol
        except Exception:
            continue
    return lookup


def _flip(doc, instance, hand):
    """Flip an instance along its hand or its facing direction.

    A family with a flip control is flipped with that control. A family with
    no flip control is mirrored in place, about the plane through its location
    point. Revit reports each mirror of such a family as "facing flipped" with
    a different rotation, also when the mirror was along the hand direction.
    Thus the check is that the mirror state changed, not the hand or facing
    property.

    Returns:
        bool: True if the instance is flipped.
    """
    if hand and instance.CanFlipHand:
        instance.flipHand()
        return True
    if not hand and instance.CanFlipFacing:
        instance.flipFacing()
        return True
    try:
        doc.Regenerate()
        before = bool(instance.Mirrored)
        direction = instance.HandOrientation if hand else instance.FacingOrientation
        plane = DB.Plane.CreateByNormalAndOrigin(direction, instance.Location.Point)
        ids = List[DB.ElementId]()
        ids.Add(instance.Id)
        DB.ElementTransformUtils.MirrorElements(doc, ids, plane, False)
        doc.Regenerate()
    except Exception:
        return False
    return bool(instance.Mirrored) != before


def create(spatial_element, entry, placement):
    """Place an entry's families into a target Area.

    Must be called inside an open transaction.

    Args:
        spatial_element: Target Area.
        entry (LayoutEntry): The layout to place.
        placement (Placement): Orientation and shift from the recommender.
    Returns:
        CreateResult
    """
    doc = spatial_element.Document
    result = CreateResult()

    frame, _, _ = describe_target(spatial_element)
    if frame is None:
        result.warnings.append("The target has no usable boundary.")
        return result

    level = get_level(spatial_element)
    symbols = _symbol_lookup(doc)

    for item, (local_x, local_y) in oriented_items(entry, placement):
        symbol = symbols.get(item.symbol_key)
        if symbol is None:
            key = "{} : {}".format(item.family, item.type)
            result.missing[key] = result.missing.get(key, 0) + 1
            continue

        if not symbol.IsActive:
            symbol.Activate()

        world_x, world_y = frame.to_world((local_x, local_y))
        point = DB.XYZ(world_x, world_y, level.ProjectElevation)

        try:
            instance = doc.Create.NewFamilyInstance(
                point, symbol, level, DB.Structure.StructuralType.NonStructural
            )
        except Exception as error:
            result.warnings.append(
                "{} : {} could not be placed: {}".format(item.family, item.type, error)
            )
            continue

        # Always set the height above the level. NewFamilyInstance with a level
        # adds the Z of the point to the level elevation, so that an item with
        # no offset would be at two times the level elevation.
        parameter = instance.get_Parameter(
            DB.BuiltInParameter.INSTANCE_ELEVATION_PARAM
        )
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(item.z_offset)

        # The size of the instance (for example the length of a counter top)
        failed = apply_instance_parameters(instance, item.parameters)
        if failed:
            result.warnings.append(
                "{} : {}: these parameters were not set: {}".format(
                    item.family, item.type, ", ".join(failed)
                )
            )

        angle = frame.angle + orient_angle(item.rotation, placement.turns, placement.mirror)
        if abs(angle) > 1e-9:
            axis = DB.Line.CreateBound(point, point + DB.XYZ.BasisZ)
            DB.ElementTransformUtils.RotateElement(doc, instance.Id, axis, angle)

        # A mirrored placement flips each item's handedness. A new instance
        # has no flip, so each wanted flip is made one time. The properties of
        # the instance are not read between the two flips: for a family with
        # no flip control, a mirror along the hand shows as "facing flipped",
        # and a second mirror would then undo the first one.
        want_hand = item.hand_flipped != placement.mirror
        for hand, wanted in ((True, want_hand), (False, item.facing_flipped)):
            if wanted and not _flip(doc, instance, hand):
                result.warnings.append(
                    "{} : {} could not be flipped; it keeps its original {}".format(
                        item.family, item.type, "hand" if hand else "facing"
                    )
                )

        result.placed.append(instance.Id)

    # A layout of which nothing was placed is not the layout of the target
    if result.placed:
        result.tagged_silos, kept = tag_data_silos(spatial_element, entry.id)
        for value in kept:
            result.warnings.append(
                "The program keeps its Unit Layout Type '{}'. It was not "
                "replaced with the layout id.".format(value)
            )
    return result


def tag_data_silos(spatial_element, entry_id):
    """Write the catalogue key into PDSO data silos standing in the target.

    A data silo (see harvest_features.pdso_kinds) on the target's level that
    carries the "PDSO - Unit Layout Type" parameter and stands inside the
    outline. A Program Volume is not written to: the sync owns its data.

    "PDSO - Unit Layout Type" is a field that users fill in (for example
    "2BR - Type A"). A value that a user typed is kept; only an empty field or
    a layout id from an earlier placement is replaced. Must be called inside
    an open transaction.

    Returns:
        tuple: (ElementIds of the silos updated, values that were kept)
    """
    doc = spatial_element.Document
    outline = get_outline(spatial_element)
    level_id = spatial_element.LevelId
    tagged, kept = [], []
    kind = pdso_kinds(doc)

    for instance in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        if kind(instance) != DATA_SILO:
            continue
        parameter = instance.LookupParameter(UNIT_LAYOUT_PARAMETER)
        if parameter is None or parameter.IsReadOnly:
            continue
        if instance.LevelId != level_id:
            continue
        location = instance.Location
        if not isinstance(location, DB.LocationPoint):
            continue
        if not point_in_polygon((location.Point.X, location.Point.Y), outline):
            continue
        current = parameter.AsString() or ""
        if current.strip() and not is_entry_id(current.strip()):
            kept.append(current)
            continue
        parameter.Set(entry_id)
        tagged.append(instance.Id)

    return tagged, kept
