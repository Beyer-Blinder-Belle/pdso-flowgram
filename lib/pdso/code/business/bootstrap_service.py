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
It adds nothing of its own, so the API and the ribbon make the same model.
Change setup behavior in setup/ and the JSON standards, not here.

Items that are there already are left alone, as Quick Setup's standards say.

Example Usage:
    report = bootstrap(doc, levels=["L1", "L2"])
"""

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from . import config_service, parameter_service
from .errors import PDSOError
from .revit_helpers import Transaction, all_levels, find_level, id_value
from pdso.code import defaults
from pdso.code.setup import revit_utils, status
from pdso.code.setup.revit_utils import find_area_scheme, find_family
from pdso.code.setup.runner import SetupContext, SetupStep, run_setup
from pdso.code.setup.standards import Standards, StandardsError
from pdso.code.setup.steps import views as views_step

logger = script.get_logger()

# The parts of a setup, in order. "configuration" and "parameters" always run
# (the parameters only if the model tracks none); the others are Quick Setup
# steps and can be chosen with bootstrap(parts=...).
SETUP_PARTS = [
    (
        "configuration",
        "The PDSO configuration (Project Config): area scheme mappings, Program "
        "Volume prefix and height, UID pattern, level mapping.",
    ),
    (
        "parameters",
        "The tracked PDSO parameters on Generic Models. 'all': the 20 built-in "
        "ones (unit data such as bedroom and kitchen counts). 'required': only "
        "Program UID, Program Stacking, Last Synced and the area parameters.",
    ),
    (
        "area_schemes",
        "One Revit area scheme for each mapping (default: PDSO - GSF, PDSO - "
        "NSF, PDSO - ZFA). A model's own schemes can be mapped instead.",
    ),
    (
        "families",
        "The Data Silo family: the point object that holds each program. In a "
        "Revit older than the shipped family, a placeholder family is built.",
    ),
    (
        "view_templates",
        "PDSO view templates: an area plan template that hides Program Volumes, "
        "and a 3D template for Program Volumes.",
    ),
    (
        "views",
        "Working views, as views.json lists them: by default an area plan of "
        "the GSF scheme on each level but Roof and Parapet, and a 3D Program "
        "Volumes view. Plans of other schemes are made when Areas are drawn "
        "in them.",
    ),
    ("schedules", "A Data Silo schedule and a Program Volume schedule."),
]
# What placing programs, drawing Areas and running the sync need
REQUIRED_FOR_SYNC = ("configuration", "parameters", "area_schemes", "families")
MINIMUM_SCOPE = {"parts": ["area_schemes", "families"], "parameters": "required"}


def bootstrap(
    doc, levels=None, area_scheme_mappings=None, parameters="all", parts=None
):
    """Make the model ready for PDSO: configuration, parameters, then Quick Setup.

    Args:
        doc: Revit Document (a project, with no open transaction)
        levels (list): Level names or ids that get the views step's area
            plans. Default: the levels views.json gives area plans to (all
            levels except the ones its exclude_levels leaves out, such as Roof
            and Parapet).
        area_scheme_mappings (dict): PDSO area parameter name -> name of the
            area scheme that gives its value. Written to the configuration
            before the area schemes and area plans are made. Use this for a
            model that has its own area schemes. Default: the configuration is
            not changed.
        parameters (str): "all" or "required" (see parameter_service.setup_default).
            Only used if the model tracks no parameters yet.
        parts (list): Quick Setup parts to run: "area_schemes", "families",
            "view_templates", "views", "schedules". Default: the parts setup.json
            turns on (the full package). MINIMUM_SCOPE lists what the sync needs.

    Returns:
        dict: "config_created", "shared_parameters", "data_silo_family",
            "area_schemes", "area_plans" (the area plans of the mapped schemes,
            each with "created"), "parts" (the parts that ran), and "setup"
            (the Quick Setup report rows: step, item, status, message)
    """
    if parameters not in parameter_service.PARAMETER_SETS:
        raise PDSOError(
            "parameters must be one of: {}".format(
                ", ".join(parameter_service.PARAMETER_SETS)
            )
        )
    # Quick Setup steps to run, checked before anything in the model changes
    standards = Standards(doc)
    try:
        all_steps = SetupStep.from_standards(standards.load("setup.json"))
    except StandardsError as error:
        raise PDSOError("Quick Setup standards: {}".format(error), 500)
    if parts is None:
        steps = [step for step in all_steps if step.enabled]
    else:
        known = [step.id for step in all_steps]
        unknown = [part for part in parts if part not in known]
        if unknown:
            raise PDSOError(
                "Unknown setup parts: {}. Use any of: {}".format(
                    ", ".join(unknown), ", ".join(known)
                )
            )
        steps = [step for step in all_steps if step.id in parts]
    chosen = [step.id for step in steps]
    level_elements = [find_level(doc, reference) for reference in levels or []]

    report = {"parts": chosen}
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
    area_plans_before = set(id_value(view.Id) for view in _area_plans(doc))

    # Parameter Manager
    report["shared_parameters"] = ensure_shared_parameters(doc, parameters)

    # Quick Setup, from the same standards folders as the ribbon button
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
        # Not chosen, or the families step reports why in "setup"
        report["data_silo_family"] = None
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

    # The area plans of the mapped schemes. Quick Setup's views step made the
    # ones views.json turns on.
    report["area_plans"] = [
        {
            "id": id_value(view.Id),
            "name": view.Name,
            "scheme": view.AreaScheme.Name,
            "level": view.GenLevel.Name,
            "created": id_value(view.Id) not in area_plans_before,
        }
        for view in _area_plans(doc)
        if view.AreaScheme is not None
        and view.GenLevel is not None
        and view.AreaScheme.Name in scheme_names
    ]
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


def ensure_shared_parameters(doc, which="all"):
    """Set up the tracked parameters, if the model tracks no parameter yet.

    A new model gets the built-in PDSO parameters ("all": the "Default" preset
    of the Parameter Manager; "required": the "Blank" preset): shared
    parameters, bound to Generic Models (instance), and saved as the tracked
    parameters of the model. A model that tracks parameters is not changed.

    Returns:
        dict: {"created": [names], "existing": [names]}
    """
    return parameter_service.setup_default(doc, which)


# =============================================================================
# Setup status (read-only)
# =============================================================================


def setup_status(doc):
    """What each setup part makes, and what the model has already. Read-only.

    For an agent to agree the scope of a setup with the user before calling
    bootstrap.

    Returns:
        dict: "parts" (one dict per part: part, description, required_for_sync,
            state "present", "partial" or "missing", details), "full_package"
            and "minimum" (bootstrap arguments)
    """
    config = config_service.get_config(doc)
    standards = Standards(doc)
    ctx = SetupContext(doc, standards)
    mappings = config.get("area_scheme_mappings") or defaults.AREA_SCHEME_MAPPINGS
    scheme_names = sorted(set(v for v in mappings.values() if v))
    states = {}

    states["configuration"] = (
        "present" if config.get("exists") else "missing",
        {"exists": bool(config.get("exists"))},
    )

    tracked = list((config.get("tethered_parameters") or {}).keys())
    states["parameters"] = (
        "present" if tracked else "missing",
        {"tracked": sorted(tracked)},
    )

    present = [name for name in scheme_names if find_area_scheme(doc, name)]
    states["area_schemes"] = (
        _state(present, scheme_names),
        {
            "present": present,
            "missing": [name for name in scheme_names if name not in present],
        },
    )

    family_name = config.get(
        "data_silo_family_name"
    ) or defaults.data_silo_family_name(doc.Application.VersionNumber)
    loaded = find_family(doc, family_name) is not None
    states["families"] = (
        "present" if loaded else "missing",
        {"data_silo_family": family_name, "loaded": loaded},
    )

    templates = [
        ctx.expand(spec.get("name", ""))
        for spec in _standards_items(standards, "view_templates.json", "view_templates")
    ]
    have = [name for name in templates if revit_utils.find_view_template(doc, name)]
    states["view_templates"] = (
        _state(have, templates),
        {"present": have, "missing": [n for n in templates if n not in have]},
    )

    views_data = _load_views_standards(standards)
    levels = _default_area_plan_levels(ctx, views_data, doc)
    missing_plans = []
    expected = 0
    for scheme_name, level in _expected_area_plans(ctx, views_data, scheme_names):
        scheme = find_area_scheme(doc, scheme_name)
        expected += 1
        if scheme is None or find_area_plan(doc, scheme, level) is None:
            missing_plans.append("{} / {}".format(scheme_name, level.Name))
    views_3d = [
        ctx.expand(spec.get("name", ""))
        for spec in (views_data or {}).get("views", [])
        if spec.get("view_type") == "ThreeD" and spec.get("enabled", True)
    ]
    have_3d = [
        name
        for name in views_3d
        if revit_utils.find_view(doc, name, DB.ViewType.ThreeD) is not None
    ]
    states["views"] = (
        _state_counts(
            expected - len(missing_plans) + len(have_3d), expected + len(views_3d)
        ),
        {
            "area_plan_levels": [level.Name for level in levels],
            "missing_area_plans": missing_plans,
            "views_3d_present": have_3d,
            "views_3d_missing": [n for n in views_3d if n not in have_3d],
        },
    )

    schedules = [
        ctx.expand(spec.get("name", ""))
        for spec in _standards_items(standards, "schedules.json", "schedules")
    ]
    have = [
        name
        for name in schedules
        if revit_utils.find_view(doc, name, view_class=DB.ViewSchedule) is not None
    ]
    states["schedules"] = (
        _state(have, schedules),
        {"present": have, "missing": [n for n in schedules if n not in have]},
    )

    parts = []
    for part, description in SETUP_PARTS:
        state, details = states[part]
        parts.append(
            {
                "part": part,
                "description": description,
                "required_for_sync": part in REQUIRED_FOR_SYNC,
                "state": state,
                "details": details,
            }
        )
    return {
        "parts": parts,
        "full_package": {
            "parts": [part for part, _ in SETUP_PARTS[2:]],
            "parameters": "all",
        },
        "minimum": dict(MINIMUM_SCOPE),
    }


def _expected_area_plans(ctx, views_data, scheme_names):
    """(scheme name, level) of each area plan the views step makes.

    From the enabled area plan entries of views.json. If views.json can't be
    read, every mapped scheme on every level.
    """
    if views_data is None:
        return [
            (scheme_name, level)
            for scheme_name in scheme_names
            for level in all_levels(ctx.doc)
        ]
    expected = []
    for spec in views_data.get("views", []):
        if spec.get("view_type") != "AreaPlan" or not spec.get("enabled", True):
            continue
        scheme_name = ctx.area_scheme_name(spec.get("area_scheme", ""))
        levels, _ = revit_utils.get_levels(
            ctx.doc, spec.get("levels", "all"), spec.get("exclude_levels") or []
        )
        expected.extend((scheme_name, level) for level in levels)
    return expected


def _state(present, expected):
    """'present', 'partial' or 'missing' from the items found and expected."""
    return _state_counts(len(present), len(expected))


def _state_counts(have, total):
    if not total or have >= total:
        return "present"
    return "partial" if have else "missing"


def _standards_items(standards, file_name, key):
    """The enabled items of a standards file, or [] if it can't be read."""
    try:
        data = standards.load(file_name).data
    except StandardsError as error:
        logger.warning("{}: {}".format(file_name, error))
        return []
    return [item for item in data.get(key, []) if item.get("enabled", True)]


# =============================================================================
# Area plans
# =============================================================================


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
    for the scheme, as Quick Setup makes it (for example "PDSO - GSF - L1"),
    even if the entry is turned off. Only a scheme with no entry in views.json
    gets a plain plan, named "<scheme> - <level>" with bold area boundary
    lines. If views.json can't be read, or its plan can't be made, this raises
    PDSOError instead of making a different plan.

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
        has_entry, problem = _create_area_plan_from_standards(
            doc, scheme_name, level, standards
        )
        view = find_area_plan(doc, scheme, level)
        if view is None and has_entry:
            raise PDSOError(
                "Couldn't create the area plan of '{}' on '{}'. {}".format(
                    scheme_name,
                    level.Name,
                    problem
                    or "A view with the name views.json gives it may exist already.",
                ),
                500,
            )
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
    """Create the plan as Quick Setup would.

    Returns:
        tuple: (has_entry, problem). has_entry is False if views.json has no
            area plan entry for the scheme; problem is what the views step
            reported as failed, or None.
    """
    standards = standards or Standards(doc)
    ctx = SetupContext(doc, standards)
    ctx.current_step = "views"
    try:
        ctx.current_file = standards.load("views.json")
    except StandardsError as error:
        raise PDSOError("views.json: {}".format(error), 500)
    has_entry = views_step.create_area_plan(
        ctx, ctx.current_file.data, scheme_name, level
    )
    problems = [
        message
        for _, _, row_status, message in ctx.results
        if row_status == status.FAILED and message
    ]
    return has_entry, " ".join(problems) or None


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
