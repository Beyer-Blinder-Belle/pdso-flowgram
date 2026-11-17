# -*- coding: utf-8 -*-
"""Route table and handlers for the PDSO HTTP API.

All routes are under ``/pdso/``. A handler takes the request context and the
path parameters, calls a UI-free service in ``pdso.code.business``, and returns
JSON-serializable data. Lengths are decimal feet. Areas are square feet.
"""

import sys
import traceback

from pyrevit import HOST_APP

from pdso.code.api.dispatcher import ApiError, dev_mode_enabled, reload_modules
from pdso.code.business import (
    area_drafting_service,
    bootstrap_service,
    config_service,
    model_service,
    parameter_service,
    program_service,
    visualization_service,
)
from pdso.code.business.audit_service import RecordingOutput, generate_detailed_report
from pdso.code.business.preflight_service import PDSOPreflightService
from pdso.code.business.revit_helpers import all_levels, find_level, level_info
from pdso.code.business import sync_service
from pdso.code.business.sync_service import SYNC_OPTIONS

ROUTES = []

# Version of the HTTP API. Increase it when a route or a reply changes. The
# MCP server compares it with the version that it was written for.
API_VERSION = "1.9"


def route(method, pattern):
    """Register a handler in the route table."""

    def wrapper(func):
        ROUTES.append((method, pattern, func))
        return func

    return wrapper


def _flag(value, default=False):
    """Read a boolean from a JSON body value or a query string value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _number(ctx, name, required=False):
    value = ctx.arg(name, required=required)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ApiError("Field '{}' must be a number.".format(name))


# =============================================================================
# Meta
# =============================================================================


@route("GET", "routes")
def list_routes(ctx):
    """List all routes."""
    return [
        {
            "method": method,
            "path": "/pdso/" + pattern,
            "summary": (func.__doc__ or "").strip().split("\n")[0],
        }
        for method, pattern, func in ROUTES
        if dev_mode_enabled() or not pattern.startswith("dev/")
    ]


@route("GET", "status")
def get_status(ctx):
    """Get the Revit version, the open document and the PDSO setup state."""
    status = {
        "revit": {"version": HOST_APP.version, "build": HOST_APP.build},
        "pyrevit": {
            "version": _pyrevit_version(),
            # False: list filters, "document" and "_rid" in a query are lost
            "query_parameters": ctx.query_supported,
        },
        "api_version": API_VERSION,
        "document": None,
        "pdso_config_exists": False,
    }
    if ctx.doc is not None:
        status["document"] = model_service.document_info(ctx.doc)
        if not ctx.doc.IsFamilyDocument:
            status["pdso_config_exists"] = config_service.config_exists(ctx.doc)
    return status


def _pyrevit_version():
    try:
        from pyrevit.versionmgr import get_pyrevit_version

        return get_pyrevit_version().get_formatted()
    except Exception:
        return None


# =============================================================================
# Document, levels, views
# =============================================================================


@route("GET", "document")
def get_document(ctx):
    """Describe the active document."""
    return model_service.document_info(ctx.require_doc())


@route("POST", "document/new")
def new_document(ctx):
    """Create a project file and open it. Body: path, template?, overwrite?"""
    return model_service.new_project(
        ctx.uiapp,
        ctx.arg("path", required=True),
        template=ctx.arg("template"),
        overwrite=_flag(ctx.arg("overwrite")),
    )


@route("POST", "document/open")
def open_document(ctx):
    """Open a project file. Body: path"""
    return model_service.open_project(ctx.uiapp, ctx.arg("path", required=True))


@route("POST", "documents/close")
@route("POST", "document/close")
def close_document(ctx):
    """Close an open document that is not the active one.

    Body: document (or title), save?. "document/close" is the older name.
    """
    title = ctx.arg("title")
    if title:
        doc = model_service.find_open_document(ctx.app, str(title))
    elif ctx.arg("document"):
        doc = ctx.require_doc()
    else:
        raise ApiError("Give the document to close ('document').")
    return model_service.close_document(doc, ctx.doc, save=_flag(ctx.arg("save")))


@route("POST", "documents/save")
@route("POST", "document/save")
def save_document(ctx):
    """Save a document to its file. Body: document? (default: the active one).

    "document/save" is the older name.
    """
    return model_service.save_project(ctx.require_doc())


@route("GET", "documents")
def list_documents(ctx):
    """List the project documents that are open in Revit."""
    return model_service.list_documents(ctx.app, ctx.doc)


@route("POST", "documents/open")
def open_document_in_background(ctx):
    """Open a project file but do not show it. Body: path, detach?"""
    return model_service.open_in_background(
        ctx.uiapp, ctx.arg("path", required=True), detach=_flag(ctx.arg("detach"))
    )


@route("GET", "levels")
def list_levels(ctx):
    """List the levels, lowest first."""
    return [level_info(level) for level in all_levels(ctx.require_doc())]


@route("POST", "levels")
def create_level(ctx):
    """Create a level. Body: name, elevation (feet)"""
    return model_service.create_level(
        ctx.require_doc(),
        ctx.arg("name", required=True),
        _number(ctx, "elevation", True),
    )


@route("GET", "views")
def list_views(ctx):
    """List the views. Query: type? (for example AreaPlan, ThreeD)"""
    return model_service.list_views(ctx.require_doc(), ctx.arg("type"))


@route("POST", "views/activate")
def activate_view(ctx):
    """Show a view in the Revit window. Body: view (name or id), type?, zoom_box?"""
    doc = ctx.require_active_doc()
    view = model_service.find_view(
        doc, ctx.arg("view", required=True), view_type=ctx.arg("type")
    )
    return model_service.activate_view(ctx.uidoc, view, zoom_box=ctx.arg("zoom_box"))


# =============================================================================
# Setup and configuration
# =============================================================================


@route("POST", "bootstrap")
def bootstrap(ctx):
    """Config, parameters, Quick Setup. Body: levels?, area_scheme_mappings?,
    parameters? ("all" or "required"), parts? (Quick Setup step ids)"""
    return bootstrap_service.bootstrap(
        ctx.require_doc(),
        levels=ctx.arg("levels"),
        area_scheme_mappings=ctx.arg("area_scheme_mappings"),
        parameters=ctx.arg("parameters") or "all",
        parts=ctx.arg("parts"),
    )


@route("GET", "setup")
def get_setup_status(ctx):
    """What each setup part makes and what the model has. Read-only."""
    return bootstrap_service.setup_status(ctx.require_doc())


@route("GET", "config")
def get_config(ctx):
    """Read the PDSO configuration of the model."""
    return config_service.get_config(ctx.require_doc())


@route("PUT", "config")
def put_config(ctx):
    """Change PDSO configuration fields. Body: the fields to change"""
    # "document" selects the document; it is not a configuration field
    updates = dict(
        (key, value) for key, value in ctx.body.items() if key != "document"
    )
    return config_service.update_config(ctx.require_doc(), updates)


@route("GET", "config/checks")
def get_config_checks(ctx):
    """Run the model checks of the Project Config window."""
    return config_service.run_model_checks(ctx.require_doc())


# =============================================================================
# Parameters: the standard PDSO parameters and the custom parameters
# =============================================================================


@route("GET", "parameters")
def list_parameters(ctx):
    """List the standard and the custom parameters of the model."""
    return parameter_service.list_parameters(ctx.require_doc())


@route("POST", "parameters")
def create_parameter(ctx):
    """Create a custom parameter. Body: name, type? (see GET parameters)"""
    return parameter_service.create_parameter(
        ctx.require_doc(), ctx.arg("name", required=True), ctx.arg("type", "string")
    )


@route("DELETE", "parameters/<ref>")
def delete_parameter(ctx, ref):
    """Delete a custom parameter (field or name). Query: purge? (delete values)"""
    return parameter_service.delete_parameter(
        ctx.require_doc(), ref, purge=_flag(ctx.arg("purge"))
    )


# =============================================================================
# Preflight, sync, audit
# =============================================================================


def _run_preflight(doc):
    service = PDSOPreflightService(doc)
    service.run_preflight_checks()
    return service.summary()


@route("GET", "preflight")
def get_preflight(ctx):
    """Run the preflight checks."""
    return _run_preflight(ctx.require_doc())


@route("GET", "sync/options")
def get_sync_options(ctx):
    """List the sync options, their defaults and their permitted values."""
    return dict(
        (name, {"default": default, "values": values})
        for name, (default, values) in SYNC_OPTIONS.items()
    )


@route("POST", "sync")
def run_sync(ctx):
    """Run the preflight checks, then the sync. Body: options?, force?"""
    doc = ctx.require_doc()
    result = sync_service.run_with_preflight(
        doc, ctx.arg("options"), force=_flag(ctx.arg("force"))
    )
    if not result["ran"]:
        return result
    try:
        result["program_volumes"] = program_service.program_volume_summary(doc)
    except Exception as error:
        result["program_volumes"] = {"error": str(error)}
    return result


@route("GET", "audit")
def get_audit(ctx):
    """Make the detailed audit report. Query: format? (markdown or blocks)"""
    doc = ctx.require_doc()
    output = RecordingOutput()
    from pyrevit import script

    generate_detailed_report(output, script.get_logger(), doc)
    report = {"element_ids": sorted(output.element_ids)}
    if ctx.arg("format", "markdown") == "blocks":
        report["blocks"] = output.blocks
    else:
        report["markdown"] = output.to_markdown()
    return report


# =============================================================================
# Programs (Data Silos): the space types and unit types of the building program
# =============================================================================


@route("GET", "programs/schema")
def get_program_schema(ctx):
    """List the fields of a program and which fields can be set."""
    return program_service.field_schema(ctx.require_doc())


@route("GET", "programs/summary")
def get_program_summary(ctx):
    """Count programs and add their areas by a field. Query: group_by?"""
    return program_service.summarize_program_types(
        ctx.require_doc(), ctx.arg("group_by", "program_type")
    )


@route("GET", "programs")
def list_programs(ctx):
    """List the programs. Query: level? and any program field as a filter"""
    # "document" selects the document; it is not a filter
    filters = dict(
        (key, value) for key, value in ctx.query.items() if key != "document"
    )
    return program_service.list_programs(ctx.require_doc(), filters)


@route("POST", "programs")
def create_program(ctx):
    """Place a program. Body: level, x, y, fields (uid is required)"""
    return program_service.create_program(
        ctx.require_doc(),
        ctx.arg("level", required=True),
        _number(ctx, "x", True),
        _number(ctx, "y", True),
        ctx.arg("fields", required=True),
    )


@route("POST", "programs/batch")
def create_programs(ctx):
    """Place many programs. Body: items = [{area_id, fields} | {level, x, y, fields}]"""
    return program_service.create_programs(
        ctx.require_doc(), ctx.arg("items", required=True)
    )


@route("PUT", "programs")
def update_programs(ctx):
    """Set fields on all programs that match. Body: filters, fields"""
    return program_service.update_programs(
        ctx.require_doc(), ctx.arg("filters"), ctx.arg("fields")
    )


@route("GET", "programs/<ref>")
def get_program(ctx, ref):
    """Describe one program (by element id or UID), its areas and its volumes."""
    return program_service.describe_program(ctx.require_doc(), ref)


@route("PUT", "programs/<ref>")
def update_program(ctx, ref):
    """Change one program. Body: fields?, x?, y?"""
    return program_service.update_program(
        ctx.require_doc(),
        ref,
        fields=ctx.arg("fields"),
        x=_number(ctx, "x"),
        y=_number(ctx, "y"),
    )


@route("DELETE", "programs/<ref>")
def delete_program(ctx, ref):
    """Delete one program (by element id or UID)."""
    return program_service.delete_program(ctx.require_doc(), ref)


# =============================================================================
# Selection
# =============================================================================


@route("GET", "selection")
def get_selection(ctx):
    """Describe the elements that are selected in Revit."""
    ctx.require_active_doc()
    return program_service.get_selection(ctx.uidoc)


@route("POST", "selection")
def set_selection(ctx):
    """Select elements in Revit and zoom to them. Body: ids, zoom?"""
    ctx.require_active_doc()
    ids = ctx.arg("ids", required=True)
    if not isinstance(ids, list):
        raise ApiError("'ids' must be a list of element ids.")
    return program_service.select_elements(
        ctx.uidoc, ids, zoom=_flag(ctx.arg("zoom"), True)
    )


# =============================================================================
# Areas and outlines
# =============================================================================


@route("GET", "area-schemes")
def list_area_schemes(ctx):
    """List the area schemes and the PDSO parameter that each one feeds."""
    return area_drafting_service.list_area_schemes(ctx.require_doc())


@route("GET", "area-plans")
def list_area_plans(ctx):
    """List the area plan views."""
    return model_service.list_views(ctx.require_doc(), "AreaPlan")


@route("POST", "area-plans")
def create_area_plan(ctx):
    """Get or create the area plan of a scheme on a level. Body: scheme, level"""
    doc = ctx.require_doc()
    return bootstrap_service.ensure_area_plan(
        doc, ctx.arg("scheme", required=True), find_level(doc, ctx.arg("level"))
    )


@route("GET", "areas")
def list_areas(ctx):
    """List the Areas with their outlines. Query: scheme?, level?, outline?"""
    return area_drafting_service.list_areas(
        ctx.require_doc(),
        scheme=ctx.arg("scheme"),
        level=ctx.arg("level"),
        with_outline=_flag(ctx.arg("outline"), True),
    )


@route("POST", "areas")
def create_area(ctx):
    """Draw an outline and place an Area. Body: scheme, level, points, name?, number?"""
    return area_drafting_service.create_area(
        ctx.require_doc(),
        ctx.arg("scheme", required=True),
        ctx.arg("level", required=True),
        ctx.arg("points", required=True),
        name=ctx.arg("name"),
        number=ctx.arg("number"),
    )


@route("POST", "areas/derive")
def derive_areas(ctx):
    """Make Areas in a scheme from Areas of another scheme, moved in by offset.

    Body: scheme, offset? (feet, default 0), and areas (ids) or from_scheme
    and level (every placed Area of that scheme on that level).
    """
    return area_drafting_service.derive_areas(
        ctx.require_doc(),
        ctx.arg("scheme", required=True),
        offset=ctx.arg("offset", 0.0),
        area_ids=ctx.arg("areas"),
        from_scheme=ctx.arg("from_scheme"),
        level=ctx.arg("level"),
    )


@route("GET", "areas/<int:area_id>")
def get_area(ctx, area_id):
    """Describe one Area with its outline."""
    doc = ctx.require_doc()
    return area_drafting_service.area_info(
        doc, area_drafting_service.get_area(doc, area_id)
    )


@route("PUT", "areas/<int:area_id>")
def update_area(ctx, area_id):
    """Change one Area. Body: one of points, move, vertex, scale; and name?, number?"""
    doc = ctx.require_doc()
    result = None
    geometry = [
        key for key in ("points", "move", "vertex", "scale") if ctx.arg(key) is not None
    ]
    if len(geometry) > 1:
        raise ApiError("Give only one of: points, move, vertex, scale.")

    if ctx.arg("name") is not None or ctx.arg("number") is not None:
        result = area_drafting_service.update_area(
            doc, area_id, name=ctx.arg("name"), number=ctx.arg("number")
        )
    if "points" in geometry:
        result = area_drafting_service.set_area_outline(doc, area_id, ctx.arg("points"))
    elif "move" in geometry:
        move = ctx.arg("move")
        result = area_drafting_service.move_area(
            doc,
            area_id,
            move.get("dx", 0),
            move.get("dy", 0),
            move_programs=_flag(move.get("move_programs"), True),
        )
    elif "vertex" in geometry:
        vertex = ctx.arg("vertex")
        result = area_drafting_service.move_area_vertex(
            doc, area_id, vertex.get("index"), vertex.get("x"), vertex.get("y")
        )
    elif "scale" in geometry:
        result = area_drafting_service.scale_area(doc, area_id, ctx.arg("scale"))

    if result is None:
        raise ApiError("Give a change: points, move, vertex, scale, name or number.")
    return result


@route("DELETE", "areas/<int:area_id>")
def delete_area(ctx, area_id):
    """Delete one Area and its own boundary lines. Query: delete_outline?"""
    return area_drafting_service.delete_area(
        ctx.require_doc(), area_id, _flag(ctx.arg("delete_outline"), True)
    )


# =============================================================================
# Visualization
# =============================================================================


@route("POST", "views/export")
def export_view(ctx):
    """Export a view to PNG (base64). Body: view? (name or id), pixel_width?"""
    doc = ctx.require_doc()
    view = model_service.find_view(doc, ctx.arg("view"))
    return visualization_service.export_view_image(
        doc, view, ctx.arg("pixel_width", 1600)
    )


@route("POST", "views/color-areas")
def color_areas(ctx):
    """Color the Areas of an area plan by a program field.

    Body: view, color_by?, labels?
    """
    doc = ctx.require_doc()
    view = model_service.find_view(doc, ctx.arg("view", required=True))
    return visualization_service.color_areas(
        doc,
        view,
        color_by=ctx.arg("color_by", "program_type"),
        labels=_flag(ctx.arg("labels"), True),
    )


@route("POST", "views/clear-colors")
def clear_colors(ctx):
    """Remove the color overlay from a view. Body: view"""
    doc = ctx.require_doc()
    view = model_service.find_view(doc, ctx.arg("view", required=True))
    return visualization_service.clear_area_colors(doc, view)


@route("POST", "views/program-volumes")
def program_volume_view(ctx):
    """Get a shaded 3D view of the Program Volumes, colored by a field.

    Body: name?, color_by?, building?, section_box?
    """
    return visualization_service.ensure_program_volume_view(
        ctx.require_doc(),
        name=ctx.arg("name", visualization_service.PROGRAM_VOLUME_VIEW),
        color_by=ctx.arg("color_by", "program_type"),
        building=ctx.arg("building", "ghost"),
        section_box=_flag(ctx.arg("section_box"), True),
    )


# =============================================================================
# Dev (only with the flag file %APPDATA%\pyRevit\pdso_api_dev.flag)
# =============================================================================


def _require_dev():
    if not dev_mode_enabled():
        raise ApiError("Dev routes are off.", 403)


@route("POST", "dev/reload")
def dev_reload(ctx):
    """Dev: drop cached pdso modules so the next request loads new code."""
    _require_dev()
    return {"reloaded": sorted(reload_modules())}


@route("POST", "dev/exec")
def dev_exec(ctx):
    """Dev: run Python code in Revit. Body: code. The code can set `result`."""
    _require_dev()
    from StringIO import StringIO

    code = ctx.arg("code", required=True)
    scope = {
        "uiapp": ctx.uiapp,
        "app": ctx.app,
        "uidoc": ctx.uidoc,
        "doc": ctx.doc,
        "result": None,
    }
    stdout = sys.stdout
    buffer = StringIO()
    sys.stdout = buffer
    error = None
    try:
        exec(code, scope)
    except Exception:
        error = traceback.format_exc()
    finally:
        sys.stdout = stdout
    result = scope.get("result")
    try:
        import json

        json.dumps(result)
    except Exception:
        result = repr(result)
    return {"result": result, "stdout": buffer.getvalue(), "error": error}
