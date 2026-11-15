# -*- coding: utf-8 -*-
"""PDSO Bootstrap Service - make a model ready for PDSO from the HTTP API.

It does what a user does with the three setup buttons, in the same order:
    1. Project Config: the PDSO configuration (Extensible Storage on ProjectInfo),
       and optionally the area scheme mappings
    2. Parameter Manager: if the model tracks no parameters yet, all built-in
       PDSO parameters, bound to Generic Models
    3. Quick Setup: the steps in setup.json (area schemes, the Data Silo family,
       view templates, working views, schedules), from the same JSON standards
       folders as the ribbon button (setup.runner.run_setup)
Then it makes sure every mapped area scheme has an area plan on each level, named
and templated as views.json says, and builds a placeholder Data Silo family if no
family file could be loaded.

Items that are there already are left alone, as Quick Setup's standards say.

Example Usage:
    report = bootstrap(doc, levels=["L1", "L2"])
"""

# Python standard libraries
import os
import tempfile

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from . import config_service, parameter_service
from .errors import PDSOError
from .family_manager_service import ProgramVolumeFamilyLoadOptions
from .revit_helpers import Transaction, all_levels, find_level, id_value
from pdso.code.setup.runner import SetupContext, SetupStep, run_setup
from pdso.code.setup.standards import Standards, StandardsError
from pdso.code.setup.steps import views as views_step

logger = script.get_logger()


# Half of the side of the Data Silo marker (a square extrusion), in feet
DATA_SILO_MARKER_HALF_SIZE = 1.0
DATA_SILO_MARKER_HEIGHT = 1.0


def bootstrap(doc, levels=None, area_scheme_mappings=None):
    """Make the model ready for PDSO: configuration, parameters, then Quick Setup.

    Args:
        doc: Revit Document (a project, with no open transaction)
        levels (list): Level names or ids that get area plans. Default: the
            levels views.json gives area plans to (all levels except the ones
            its exclude_levels leaves out, such as Roof and Parapet).
        area_scheme_mappings (dict): PDSO area parameter name -> name of the
            area scheme that gives its value. Written to the configuration
            before the area schemes and area plans are made. Use this for a
            model that has its own area schemes. Default: the configuration is
            not changed.

    Returns:
        dict: "config_created", "shared_parameters", "data_silo_family",
            "area_schemes", "area_plans", and "setup" (the Quick Setup report
            rows: step, item, status, message)
    """
    report = {}
    report["config_created"] = config_service.initialize_config(doc)
    if area_scheme_mappings:
        config_service.update_config(
            doc, {"area_scheme_mappings": area_scheme_mappings}
        )
    config = config_service.get_config(doc)
    scheme_names = sorted(set(v for v in config["area_scheme_mappings"].values() if v))
    schemes_before = set(name for name in scheme_names if find_area_scheme(doc, name))
    families_before = set(
        id_value(family.Id)
        for family in DB.FilteredElementCollector(doc).OfClass(DB.Family)
    )
    level_elements = [find_level(doc, reference) for reference in levels or []]
    area_plans_before = set(id_value(view.Id) for view in _area_plans(doc))

    # Parameter Manager
    report["shared_parameters"] = ensure_shared_parameters(doc)

    # Quick Setup, from the same standards folders as the ribbon button
    standards = Standards(doc)
    try:
        steps = [
            step
            for step in SetupStep.from_standards(standards.load("setup.json"))
            if step.enabled
        ]
    except StandardsError as error:
        raise PDSOError("Quick Setup standards: {}".format(error), 500)
    options = {}
    if level_elements:
        options["levels"] = [level.Name for level in level_elements]
    ctx = run_setup(doc, standards, steps, options)
    rows = [
        {"step": step, "item": item, "status": row_status, "message": message}
        for step, item, row_status, message in ctx.results
    ]

    # The families step may have pointed Project Config at the loaded family
    config = config_service.get_config(doc)
    family_name = config["data_silo_family_name"]
    family = find_family(doc, family_name)
    if family is None:
        report["data_silo_family"] = ensure_data_silo_family(doc, family_name)
        rows.append(
            {
                "step": "families",
                "item": family_name,
                "status": "Created",
                "message": "No Data Silo family file could be loaded, so a "
                "placeholder family was built. Its parameters come from the "
                "Generic Models binding.",
            }
        )
    else:
        report["data_silo_family"] = {
            "name": family_name,
            "id": id_value(family.Id),
            "created": id_value(family.Id) not in families_before,
        }

    report["area_schemes"] = {
        "created": [
            name
            for name in scheme_names
            if name not in schemes_before and find_area_scheme(doc, name)
        ],
        "existing": [name for name in scheme_names if name in schemes_before],
    }

    # One area plan for each mapped scheme and level. Quick Setup made the ones
    # views.json turns on; this adds the rest with the same names and template.
    views_data = _load_views_standards(standards)
    if not level_elements:
        level_elements = _default_area_plan_levels(ctx, views_data, doc)
    plans = []
    for scheme_name in scheme_names:
        if find_area_scheme(doc, scheme_name) is None:
            continue  # the area_schemes step reported why
        for level in level_elements:
            plan = ensure_area_plan(doc, scheme_name, level, standards=standards)
            # Made by this bootstrap: by Quick Setup's views step or just now
            plan["created"] = plan["id"] not in area_plans_before
            plans.append(plan)
    report["area_plans"] = plans
    report["setup"] = rows
    return report


def _area_plans(doc):
    """Every area plan view in the model (not templates)."""
    return [
        view
        for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan)
        if not view.IsTemplate and view.ViewType == DB.ViewType.AreaPlan
    ]


def _load_views_standards(standards):
    """The data of views.json, or None if it can't be read."""
    try:
        return standards.load("views.json").data
    except StandardsError as error:
        logger.warning("views.json: {}".format(error))
        return None


def _default_area_plan_levels(ctx, views_data, doc):
    if views_data is None:
        return all_levels(doc)
    return views_step.default_area_plan_levels(ctx, views_data)


# =============================================================================
# Shared parameters
# =============================================================================


def ensure_shared_parameters(doc):
    """Set up the tracked parameters, if the model tracks no parameter yet.

    A new model gets all built-in PDSO parameters (the "Default" preset of the
    Parameter Manager): shared parameters, bound to Generic Models (instance),
    and saved as the tracked parameters of the model. A model that tracks
    parameters is not changed.

    Returns:
        dict: {"created": [names], "existing": [names]}
    """
    return parameter_service.setup_default(doc)


# =============================================================================
# Data Silo family
# =============================================================================


def find_family(doc, family_name):
    """Find a loaded Family by name, or None."""
    for family in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if family.Name == family_name:
            return family
    return None


def _generic_model_template(app):
    path = os.path.join(app.FamilyTemplatePath, "English-Imperial", "Generic Model.rft")
    if os.path.exists(path):
        return path
    for root, _, files in os.walk(app.FamilyTemplatePath):
        if "Generic Model.rft" in files:
            return os.path.join(root, "Generic Model.rft")
    raise PDSOError(
        "Family template 'Generic Model.rft' not found in {}".format(
            app.FamilyTemplatePath
        ),
        500,
    )


def ensure_data_silo_family(doc, family_name):
    """Load a Data Silo family with the configured name if there is none.

    The new family is a Generic Model with a small square marker. The PDSO
    parameters come from the project binding (see ensure_shared_parameters).

    Returns:
        dict: {"name", "id", "created"}
    """
    family = find_family(doc, family_name)
    if family is not None:
        return {"name": family_name, "id": id_value(family.Id), "created": False}

    app = doc.Application
    family_doc = app.NewFamilyDocument(_generic_model_template(app))
    if family_doc is None:
        raise PDSOError("Revit could not make a new family document.", 500)

    try:
        with Transaction(family_doc, "PDSO - Build Data Silo Marker"):
            plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ.Zero)
            sketch_plane = DB.SketchPlane.Create(family_doc, plane)
            half = DATA_SILO_MARKER_HALF_SIZE
            corners = [
                DB.XYZ(-half, -half, 0),
                DB.XYZ(half, -half, 0),
                DB.XYZ(half, half, 0),
                DB.XYZ(-half, half, 0),
            ]
            loop = DB.CurveArray()
            for index in range(4):
                loop.Append(
                    DB.Line.CreateBound(corners[index], corners[(index + 1) % 4])
                )
            profile = DB.CurveArrArray()
            profile.Append(loop)
            family_doc.FamilyCreate.NewExtrusion(
                True, profile, sketch_plane, DATA_SILO_MARKER_HEIGHT
            )

        folder = os.path.join(tempfile.gettempdir(), "pdso_families")
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = os.path.join(folder, family_name + ".rfa")
        options = DB.SaveAsOptions()
        options.OverwriteExistingFile = True
        family_doc.SaveAs(path, options)
        family = family_doc.LoadFamily(doc, ProgramVolumeFamilyLoadOptions(True, True))
    finally:
        family_doc.Close(False)

    if family is None:
        family = find_family(doc, family_name)
    if family is None:
        raise PDSOError("Could not load the Data Silo family.", 500)
    return {"name": family_name, "id": id_value(family.Id), "created": True}


# =============================================================================
# Area schemes and area plans
# =============================================================================


def find_area_scheme(doc, name):
    """Find an AreaScheme by name, or None."""
    for scheme in DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme):
        if scheme.Name == name:
            return scheme
    return None


def find_area_plan(doc, scheme, level):
    """Find the area plan of a scheme on a level, or None."""
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if view.IsTemplate or view.ViewType != DB.ViewType.AreaPlan:
            continue
        if view.AreaScheme is None or view.GenLevel is None:
            continue
        if view.AreaScheme.Id == scheme.Id and view.GenLevel.Id == level.Id:
            return view
    return None


def ensure_area_plan(doc, scheme_name, level, standards=None):
    """Get the area plan of a scheme on a level. Create it if there is none.

    A new plan gets the name and view template of views.json's area plan entry
    for the scheme, as Quick Setup makes it (for example "PDSO - GSF - L1").
    Without such an entry, it is named "<scheme> - <level>" and gets bold area
    boundary lines.

    Args:
        standards (Standards): Standards to read views.json from. Default: the
            model's standards folders, as Quick Setup uses them.

    Returns:
        dict: {"id", "name", "scheme", "level", "created"}
    """
    scheme = find_area_scheme(doc, scheme_name)
    if scheme is None:
        raise PDSOError("Area scheme '{}' not found.".format(scheme_name), 404)

    view = find_area_plan(doc, scheme, level)
    created = False
    if view is None:
        _create_area_plan_from_standards(doc, scheme_name, level, standards)
        view = find_area_plan(doc, scheme, level)
        created = view is not None
    if view is None:
        view = _create_plain_area_plan(doc, scheme, scheme_name, level)
        created = True

    return {
        "id": id_value(view.Id),
        "name": view.Name,
        "scheme": scheme_name,
        "level": level.Name,
        "created": created,
    }


def _create_area_plan_from_standards(doc, scheme_name, level, standards=None):
    """Create the plan as Quick Setup would. Returns False if that isn't possible."""
    try:
        standards = standards or Standards(doc)
        ctx = SetupContext(doc, standards)
        ctx.current_step = "views"
        ctx.current_file = standards.load("views.json")
        return views_step.create_area_plan(
            ctx, ctx.current_file.data, scheme_name, level
        )
    except StandardsError as error:
        logger.warning("views.json: {}".format(error))
    except Exception as error:
        logger.warning(
            "Could not create the area plan of '{}' on '{}' from views.json: "
            "{}".format(scheme_name, level.Name, error)
        )
    return False


def _create_plain_area_plan(doc, scheme, scheme_name, level):
    """Create an area plan named "<scheme> - <level>" with bold boundary lines."""
    with Transaction(doc, "PDSO - Create Area Plan"):
        view = DB.ViewPlan.CreateAreaPlan(doc, scheme.Id, level.Id)
        try:
            view.Name = "{} - {}".format(scheme_name, level.Name)
        except Exception:
            pass  # The name is in use. Keep the name that Revit gave.
        for category in (
            DB.BuiltInCategory.OST_Elev,
            DB.BuiltInCategory.OST_Sections,
            DB.BuiltInCategory.OST_Grids,
            DB.BuiltInCategory.OST_CLines,
            DB.BuiltInCategory.OST_Dimensions,
        ):
            try:
                view.SetCategoryHidden(DB.ElementId(category), True)
            except Exception:
                pass
        # Bold area boundary lines, so that the outlines show on a real plan
        try:
            settings = DB.OverrideGraphicSettings()
            settings.SetProjectionLineWeight(7)
            settings.SetProjectionLineColor(DB.Color(196, 0, 122))
            view.SetCategoryOverrides(
                DB.ElementId(DB.BuiltInCategory.OST_AreaSchemeLines), settings
            )
        except Exception:
            pass
    return view
