# -*- coding: utf-8 -*-
"""PDSO Visualization Service - images and color overlays of the program.

Revit does not fill an Area with a graphic override. To show the program in an
area plan, this service draws an overlay: one filled region and one text label
per Area, colored by a program field. The overlay is view-specific and can be
removed (see clear_area_colors).

Example Usage:
    legend = color_areas(doc, view, color_by="program_type")
    image = export_view_image(doc, view, pixel_width=1600)
"""

# Python standard libraries
import base64
import glob
import os
import tempfile

# System Modules
from System.Collections.Generic import List

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from . import (
    area_drafting_service,
    config_service,
    parameter_service,
    program_service,
)
from .errors import PDSOError
from .revit_helpers import (
    Transaction,
    TransactionGroup,
    id_list,
    id_value,
    point_in_area,
    polygon_interior_point,
)

logger = script.get_logger()

OVERLAY_TYPE_PREFIX = "PDSO Viz - "
LABEL_TYPE_NAME = "PDSO Viz Label"
NO_PROGRAM = "(no program)"
NO_PROGRAM_COLOR = (200, 200, 200)

# Label text height limits (feet, in the model) and text metrics as a ratio of
# the text height
LABEL_MIN_HEIGHT = 0.4
LABEL_MAX_HEIGHT = 3.0
LABEL_LINE_SPACING = 2.0
LABEL_CHARACTER_WIDTH = 0.62

# Colors for the values of the color-by field, in order of first use
PALETTE = [
    (66, 133, 244),
    (234, 67, 53),
    (251, 188, 5),
    (52, 168, 83),
    (171, 71, 188),
    (0, 172, 193),
    (255, 112, 67),
    (124, 179, 66),
    (92, 107, 192),
    (240, 98, 146),
    (141, 110, 99),
    (38, 166, 154),
]


def _hex(color):
    return "#{:02x}{:02x}{:02x}".format(*color)


# =============================================================================
# Image export
# =============================================================================


def export_view_image(doc, view, pixel_width=1600):
    """Export a view to a PNG image.

    Args:
        doc: Revit Document
        view: View element
        pixel_width (int): Width of the image in pixels (200 to 6000)

    Returns:
        dict: {"view": {...}, "file": path on the Revit computer,
            "image_base64": PNG bytes as base64}
    """
    pixel_width = int(pixel_width)
    if pixel_width < 200 or pixel_width > 6000:
        raise PDSOError("pixel_width must be 200 to 6000.")
    if not view.CanBePrinted:
        raise PDSOError("View '{}' cannot be exported.".format(view.Name))

    folder = os.path.join(tempfile.gettempdir(), "pdso_exports")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    prefix = "view_{}".format(id_value(view.Id))
    for old_file in glob.glob(os.path.join(folder, prefix + "*")):
        os.remove(old_file)

    options = DB.ImageExportOptions()
    options.ExportRange = DB.ExportRange.SetOfViews
    options.SetViewsAndSheets(id_list([view.Id]))
    options.FilePath = os.path.join(folder, prefix)
    options.HLRandWFViewsFileType = DB.ImageFileType.PNG
    options.ShadowViewsFileType = DB.ImageFileType.PNG
    options.ZoomType = DB.ZoomFitType.FitToPage
    options.FitDirection = DB.FitDirectionType.Horizontal
    options.PixelSize = pixel_width
    options.ImageResolution = DB.ImageResolution.DPI_150
    doc.ExportImage(options)

    files = glob.glob(os.path.join(folder, prefix + "*.png"))
    if not files:
        raise PDSOError("Revit did not write an image for the view.", 500)
    with open(files[0], "rb") as image_file:
        data = image_file.read()
    return {
        "view": {
            "id": id_value(view.Id),
            "name": view.Name,
            "type": str(view.ViewType),
        },
        "file": files[0],
        "image_base64": base64.b64encode(data),
    }


# =============================================================================
# Area color overlay
# =============================================================================


def _solid_fill_id(doc):
    for pattern in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
        if pattern.GetFillPattern().IsSolidFill:
            return pattern.Id
    raise PDSOError("The model has no solid fill pattern.", 500)


def _region_type(doc, color):
    """Get the filled region type of a color. Make it if there is none."""
    name = OVERLAY_TYPE_PREFIX + _hex(color)
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType))
    for region_type in types:
        if DB.Element.Name.GetValue(region_type) == name:
            return region_type
    if not types:
        raise PDSOError("The model has no filled region type to copy.", 500)
    region_type = types[0].Duplicate(name)
    region_type.ForegroundPatternId = _solid_fill_id(doc)
    region_type.ForegroundPatternColor = DB.Color(color[0], color[1], color[2])
    region_type.IsMasking = False
    return region_type


def _label_type(doc, paper_text_size):
    """Get the text type of the overlay labels. Make it if there is none.

    Args:
        doc: Revit Document
        paper_text_size (float): Text height on paper (feet)
    """
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.TextNoteType))
    label_type = None
    for text_type in types:
        if DB.Element.Name.GetValue(text_type) == LABEL_TYPE_NAME:
            label_type = text_type
            break
    if label_type is None:
        if not types:
            raise PDSOError("The model has no text type to copy.", 500)
        label_type = types[0].Duplicate(LABEL_TYPE_NAME)
    label_type.get_Parameter(DB.BuiltInParameter.TEXT_SIZE).Set(paper_text_size)
    label_type.get_Parameter(DB.BuiltInParameter.TEXT_BACKGROUND).Set(1)  # transparent
    label_type.get_Parameter(DB.BuiltInParameter.TEXT_BOX_VISIBILITY).Set(0)
    return label_type


def _label_lines(area, program, value):
    lines = [area["name"] or area["number"] or "Area"]
    if program is not None:
        lines.append(program.get("uid") or "")
        lines.append(value)
    lines.append("{:,.0f} SF".format(area["area"]))
    return [line for line in lines if line]


def _bounds(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _model_text_height(labels):
    """Largest text height (feet, in the model) that fits all labels.

    Args:
        labels (list[tuple]): (outline, lines) for each label
    """
    height = LABEL_MAX_HEIGHT
    for outline, lines in labels:
        min_x, min_y, max_x, max_y = _bounds(outline)
        longest = max(len(line) for line in lines)
        height = min(
            height,
            (max_y - min_y) / (len(lines) * LABEL_LINE_SPACING + 1.0),
            (max_x - min_x) / (longest * LABEL_CHARACTER_WIDTH + 1.0),
        )
    return max(height, LABEL_MIN_HEIGHT)


def fit_view_to_outlines(view, outlines, margin_ratio=0.06):
    """Crop a plan view to the given outlines, plus a margin."""
    points = [point for outline in outlines for point in outline]
    if not points:
        return
    min_x, min_y, max_x, max_y = _bounds(points)
    margin = max((max_x - min_x), (max_y - min_y)) * margin_ratio
    box = view.CropBox
    to_view = box.Transform.Inverse
    low = to_view.OfPoint(DB.XYZ(min_x - margin, min_y - margin, 0))
    high = to_view.OfPoint(DB.XYZ(max_x + margin, max_y + margin, 0))
    box.Min = DB.XYZ(min(low.X, high.X), min(low.Y, high.Y), box.Min.Z)
    box.Max = DB.XYZ(max(low.X, high.X), max(low.Y, high.Y), box.Max.Z)
    view.CropBox = box
    view.CropBoxActive = True
    view.CropBoxVisible = False


def _overlay_ids(doc, view):
    ids = []
    for region in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.FilledRegion):
        region_type = doc.GetElement(region.GetTypeId())
        if region_type is not None and DB.Element.Name.GetValue(region_type).startswith(
            OVERLAY_TYPE_PREFIX
        ):
            ids.append(region.Id)
    for note in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.TextNote):
        note_type = doc.GetElement(note.GetTypeId())
        if note_type is None:
            continue
        if DB.Element.Name.GetValue(note_type) == LABEL_TYPE_NAME:
            ids.append(note.Id)
    return ids


def clear_area_colors(doc, view):
    """Remove the color overlay from a view.

    Returns:
        dict: {"removed": number of overlay elements removed}
    """
    ids = _overlay_ids(doc, view)
    if ids:
        with Transaction(doc, "PDSO - Clear Area Colors"):
            doc.Delete(id_list(ids))
    return {"removed": len(ids)}


def _require_area_plan(view):
    if view.ViewType != DB.ViewType.AreaPlan or view.AreaScheme is None:
        raise PDSOError(
            "View '{}' is not an area plan. Use an area plan view.".format(view.Name)
        )


def program_by_area(doc, areas, level):
    """Find the program in each Area.

    Args:
        doc: Revit Document
        areas (list[dict]): Areas from area_drafting_service.list_areas
        level: Level element of the Areas

    Returns:
        dict: Area id -> program dict (the first program in the Area), or None
    """
    programs = []
    if config_service.config_exists(doc):
        programs = [
            program
            for program in program_service.list_programs(doc)
            if program["level"] == level.Name and program["location"]
        ]
    result = {}
    for area in areas:
        result[area["id"]] = None
        if not area["outline"]:
            continue
        for program in programs:
            location = program["location"]
            if point_in_area(
                location["x"], location["y"], area["outline"], area.get("holes")
            ):
                result[area["id"]] = program
                break
    return result


def color_areas(doc, view, color_by="program_type", labels=True):
    """Color the Areas of an area plan by a program field.

    Args:
        doc: Revit Document
        view: Area plan view
        color_by (str): Program field that gives the color (see
            GET /pdso/programs/schema)
        labels (bool): Also write a text label in each Area

    Returns:
        dict: {"view", "color_by", "legend": [{"value", "color", "count",
            "area"}], "areas_colored"}
    """
    _require_area_plan(view)
    if color_by not in parameter_service.by_field(doc):
        raise PDSOError("Unknown program field: '{}'".format(color_by))

    level = view.GenLevel
    areas = area_drafting_service.list_areas(
        doc, scheme=view.AreaScheme.Name, level=id_value(level.Id)
    )
    programs = program_by_area(doc, areas, level)

    legend = {}
    colors = {}

    def color_of(value):
        if value == NO_PROGRAM:
            return NO_PROGRAM_COLOR
        if value not in colors:
            colors[value] = PALETTE[len(colors) % len(PALETTE)]
        return colors[value]

    drawable = []
    for area in areas:
        if len(area["outline"]) < 3:
            continue
        program = programs[area["id"]]
        value = NO_PROGRAM
        if program is not None:
            raw = program.get(color_by)
            value = str(raw) if raw not in (None, "") else "(blank)"
        drawable.append((area, program, value, _label_lines(area, program, value)))

    colored = 0
    elevation = level.ProjectElevation

    def loop_of(points):
        loop = DB.CurveLoop()
        for index in range(len(points)):
            start = points[index]
            end = points[(index + 1) % len(points)]
            loop.Append(
                DB.Line.CreateBound(
                    DB.XYZ(start[0], start[1], elevation),
                    DB.XYZ(end[0], end[1], elevation),
                )
            )
        return loop

    # Removing the old overlay and drawing the new one are one undo step
    with TransactionGroup(doc, "PDSO - Color Areas"):
        clear_area_colors(doc, view)
        with Transaction(doc, "PDSO - Draw Area Colors"):
            colored = _draw_area_colors(
                doc, view, drawable, labels, color_of, legend, loop_of, elevation
            )

    return {
        "view": {"id": id_value(view.Id), "name": view.Name},
        "color_by": color_by,
        "legend": sorted(legend.values(), key=lambda entry: entry["value"]),
        "areas_colored": colored,
    }


def _draw_area_colors(doc, view, drawable, labels, color_of, legend, loop_of, elevation):
    """Draw the filled regions and labels of color_areas. Call in a transaction."""
    colored = 0
    label_type = None
    text_height = 0.0
    if labels and drawable:
        text_height = _model_text_height(
            [(area["outline"], lines) for area, _, _, lines in drawable]
        )
        label_type = _label_type(doc, text_height / float(view.Scale))
    fit_view_to_outlines(view, [area["outline"] for area, _, _, _ in drawable])

    for area, program, value, lines in drawable:
        outline = area["outline"]
        color = color_of(value)

        # The holes of the Area are holes of the filled region too
        try:
            loops = [loop_of(outline)] + [
                loop_of(hole) for hole in area.get("holes") or [] if len(hole) >= 3
            ]
            DB.FilledRegion.Create(
                doc, _region_type(doc, color).Id, view.Id, _curve_loop_list(loops)
            )
        except Exception:
            # A hole with a very short edge, or one that touches the outline,
            # makes an invalid region: fill the outline alone rather than nothing
            DB.FilledRegion.Create(
                doc,
                _region_type(doc, color).Id,
                view.Id,
                _curve_loop_list([loop_of(outline)]),
            )

        if label_type is not None:
            center_x, center_y = polygon_interior_point(outline)
            block_height = text_height * LABEL_LINE_SPACING * len(lines)
            options = DB.TextNoteOptions(label_type.Id)
            options.HorizontalAlignment = DB.HorizontalTextAlignment.Center
            DB.TextNote.Create(
                doc,
                view.Id,
                DB.XYZ(center_x, center_y + block_height / 2.0, elevation),
                "\n".join(lines),
                options,
            )

        entry = legend.setdefault(
            value, {"value": value, "color": _hex(color), "count": 0, "area": 0.0}
        )
        entry["count"] += 1
        entry["area"] += area["area"]
        colored += 1
    return colored


def _curve_loop_list(loops):
    return List[DB.CurveLoop](loops)


# =============================================================================
# Program Volume 3D view
# =============================================================================

# Not Quick Setup's "PDSO - Program Volumes - 3D" working view: this one shows
# the building ghosted around the volumes, so it can't use that view's template
PROGRAM_VOLUME_VIEW = "PDSO - Program Volumes - Colored"
# Its name before; a view with this name is renamed to PROGRAM_VOLUME_VIEW
_OLD_PROGRAM_VOLUME_VIEW = "PDSO - Program Volumes"


def ensure_program_volume_view(
    doc,
    name=PROGRAM_VOLUME_VIEW,
    color_by="program_type",
    building="ghost",
    section_box=True,
):
    """Get a shaded 3D view that colors the Program Volumes by a program field.

    Returns:
        dict: {"view": {"id", "name"}, "legend": [...], "volumes_colored"}
    """
    if color_by not in parameter_service.by_field(doc):
        raise PDSOError("Unknown program field: '{}'".format(color_by))
    if building not in ("ghost", "show"):
        raise PDSOError("'building' must be 'ghost' or 'show'.")
    config = config_service.get_config(doc)
    if not config["exists"]:
        raise PDSOError("The model has no PDSO configuration.", 409)
    prefix = config["program_volume_family_name_prefix"]

    view = None
    old_view = None
    for candidate in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
        if candidate.IsTemplate:
            continue
        if candidate.Name == name:
            view = candidate
            break
        if name == PROGRAM_VOLUME_VIEW and candidate.Name == _OLD_PROGRAM_VOLUME_VIEW:
            old_view = candidate

    value_by_family = {}
    for program in program_service.list_programs(doc):
        raw = program.get(color_by)
        value_by_family[prefix + (program.get("uid") or "")] = (
            str(raw) if raw not in (None, "") else "(blank)"
        )

    legend = {}
    colors = {}
    colored = 0
    corners = []
    with Transaction(doc, "PDSO - Program Volume View"):
        if view is None and old_view is not None:
            view = old_view
            view.Name = name
        if view is None:
            view_type = next(
                view_family_type
                for view_family_type in DB.FilteredElementCollector(doc).OfClass(
                    DB.ViewFamilyType
                )
                if view_family_type.ViewFamily == DB.ViewFamily.ThreeDimensional
            )
            view = DB.View3D.CreateIsometric(doc, view_type.Id)
            view.Name = name
        view.DisplayStyle = DB.DisplayStyle.ShadingWithEdges
        view.DetailLevel = DB.ViewDetailLevel.Fine
        for category in (DB.BuiltInCategory.OST_Levels, DB.BuiltInCategory.OST_Grids):
            try:
                view.SetCategoryHidden(DB.ElementId(category), True)
            except Exception:
                pass
        # The building around the volumes: transparent ("ghost") or as it is
        ghost = DB.OverrideGraphicSettings()
        if building == "ghost":
            ghost.SetSurfaceTransparency(88)
            ghost.SetHalftone(True)
        generic_models = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
        for category in doc.Settings.Categories:
            if category.CategoryType != DB.CategoryType.Model:
                continue
            if category.Id == generic_models:
                continue
            try:
                view.SetCategoryOverrides(category.Id, ghost)
            except Exception:
                pass
        solid_fill = _solid_fill_id(doc)
        for instance in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
            family_name = instance.Symbol.Family.Name
            if not family_name.startswith(prefix):
                continue
            value = value_by_family.get(family_name, NO_PROGRAM)
            if value == NO_PROGRAM:
                color = NO_PROGRAM_COLOR
            else:
                if value not in colors:
                    colors[value] = PALETTE[len(colors) % len(PALETTE)]
                color = colors[value]
            settings = DB.OverrideGraphicSettings()
            settings.SetSurfaceForegroundPatternId(solid_fill)
            settings.SetSurfaceForegroundPatternColor(
                DB.Color(color[0], color[1], color[2])
            )
            view.SetElementOverrides(instance.Id, settings)
            bounds = instance.get_BoundingBox(None)
            if bounds is not None:
                for x in (bounds.Min.X, bounds.Max.X):
                    for y in (bounds.Min.Y, bounds.Max.Y):
                        for z in (bounds.Min.Z, bounds.Max.Z):
                            corners.append(DB.XYZ(x, y, z))
            entry = legend.setdefault(
                value, {"value": value, "color": _hex(color), "count": 0}
            )
            entry["count"] += 1
            colored += 1

    if corners:
        with Transaction(doc, "PDSO - Fit Program Volume View"):
            if section_box:
                box = DB.BoundingBoxXYZ()
                box.Min = DB.XYZ(
                    min(point.X for point in corners) - 6.0,
                    min(point.Y for point in corners) - 6.0,
                    min(point.Z for point in corners) - 1.0,
                )
                box.Max = DB.XYZ(
                    max(point.X for point in corners) + 6.0,
                    max(point.Y for point in corners) + 6.0,
                    max(point.Z for point in corners) + 0.5,
                )
                view.SetSectionBox(box)
                try:
                    view.SetCategoryHidden(
                        DB.ElementId(DB.BuiltInCategory.OST_SectionBox), True
                    )
                except Exception:
                    pass
            else:
                view.IsSectionBoxActive = False
            _fit_view_to_points(view, corners)

    return {
        "view": {"id": id_value(view.Id), "name": view.Name},
        "color_by": color_by,
        "legend": sorted(legend.values(), key=lambda entry: entry["value"]),
        "volumes_colored": colored,
    }


def _fit_view_to_points(view, points, margin_ratio=0.08):
    """Crop a view to the given model points (XYZ), plus a margin."""
    box = view.CropBox
    to_view = box.Transform.Inverse
    projected = [to_view.OfPoint(point) for point in points]
    min_x = min(point.X for point in projected)
    max_x = max(point.X for point in projected)
    min_y = min(point.Y for point in projected)
    max_y = max(point.Y for point in projected)
    margin = max(max_x - min_x, max_y - min_y) * margin_ratio
    box.Min = DB.XYZ(min_x - margin, min_y - margin, box.Min.Z)
    box.Max = DB.XYZ(max_x + margin, max_y + margin, box.Max.Z)
    view.CropBox = box
    view.CropBoxActive = True
    view.CropBoxVisible = False

