# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2,<3", "pillow>=10.1", "shapely>=2"]
# ///
"""MCP server for PDSO Flowgram.

This server runs on your computer (macOS, Windows or Linux). It sends each tool
call to the PDSO HTTP API in Revit (see PDSO.extension/startup.py). Revit can be
on the same computer, or on a different computer: then set PDSO_SSH_HOST and
the server opens an SSH port forward to it.

Environment:
    PDSO_API_URL      Base URL of the API. Default: http://127.0.0.1:48884/pdso
    PDSO_SSH_HOST     SSH target of the Revit computer (for example user@host).
                      If set, and the API does not answer, the server runs
                      ``ssh -N -L <local port>:127.0.0.1:<PDSO_REMOTE_PORT> <host>``.
                      Do not set it when Revit is on this computer.
    PDSO_REMOTE_PORT  pyRevit Routes port on the Revit computer. Default: 48884
    PDSO_OUTPUT_DIR   Folder for the images. Default: <temp dir>/pdso-mcp

The API calls never use a proxy (HTTP_PROXY, HTTPS_PROXY and the Windows system
proxy do not apply to them), because the API is always on 127.0.0.1.

Units: all lengths are decimal feet and all areas are square feet.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

API_URL = os.environ.get("PDSO_API_URL", "http://127.0.0.1:48884/pdso").rstrip("/")
SSH_HOST = os.environ.get("PDSO_SSH_HOST", "")
REMOTE_PORT = int(os.environ.get("PDSO_REMOTE_PORT", "48884"))
OUTPUT_DIR = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "PDSO_OUTPUT_DIR", str(Path(tempfile.gettempdir()) / "pdso-mcp")
            )
        )
    )
)

DEFAULT_TIMEOUT = 120
LONG_TIMEOUT = 1800  # sync and bootstrap can take minutes
TUNNEL_WAIT = 15  # seconds to wait for the SSH port forward

# An empty ProxyHandler turns off all proxies. Without it, urllib reads
# HTTP_PROXY and, on Windows, the system proxy in the registry, and can send the
# 127.0.0.1 API calls to the proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

mcp = MCPServer(
    "pdso",
    instructions=(
        "Tools for the PDSO Flowgram Revit plugin: programs (room types and unit "
        "types, stored as Data Silos), Area outlines, Program Volumes, sync and "
        "audit. All lengths are decimal feet and all areas are square feet. Call "
        "get_status first. A model that is new to PDSO needs setup_model one time. "
        "After you change programs or areas, call run_sync to update the areas of "
        "the programs and the Program Volumes. When the user describes data that "
        "no program field holds, call create_parameter, then set the new field with "
        "update_program or update_programs. In a model that has its own Areas and "
        "area schemes, give the scheme names to setup_model (area_scheme_mappings) "
        "and place the programs with create_programs."
    ),
)


# =============================================================================
# Connection
# =============================================================================

_tunnel: subprocess.Popen | None = None


def _close_tunnel() -> None:
    if _tunnel is not None and _tunnel.poll() is None:
        _tunnel.terminate()


atexit.register(_close_tunnel)


def _open_tunnel() -> None:
    """Start the SSH port forward to the Revit computer."""
    global _tunnel
    if _tunnel is not None and _tunnel.poll() is None:
        return
    local_port = urllib.parse.urlparse(API_URL).port or 80
    try:
        _tunnel = subprocess.Popen(
            [
                "ssh",
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "BatchMode=yes",
                "-L",
                f"127.0.0.1:{local_port}:127.0.0.1:{REMOTE_PORT}",
                SSH_HOST,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Windows: no console window for ssh.exe (0 on other systems)
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        raise ToolError(
            f"Cannot start ssh for PDSO_SSH_HOST={SSH_HOST} ({error}). Install an "
            "OpenSSH client, or unset PDSO_SSH_HOST if Revit is on this computer."
        )
    # Wait until the forward accepts connections, or ssh stops (login failed).
    deadline = time.monotonic() + TUNNEL_WAIT
    while time.monotonic() < deadline and _tunnel.poll() is None:
        try:
            socket.create_connection(("127.0.0.1", local_port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.25)


def _request(method: str, path: str, body: Any, query: dict, timeout: int) -> Any:
    query = {key: value for key, value in query.items() if value is not None}
    url = f"{API_URL}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        raw = _OPENER.open(request, timeout=timeout).read()
    except urllib.error.HTTPError as error:
        raw = error.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ToolError(f"The PDSO API gave a reply that is not JSON: {raw[:300]!r}")
    if isinstance(payload, dict) and "exception" in payload:
        raise ToolError(f"pyRevit Routes error: {payload['exception'].get('message')}")
    if not payload.get("ok"):
        error = payload.get("error", {})
        message = error.get("message", "Unknown error")
        if error.get("type") not in (None, "ApiError") and error.get("traceback"):
            message += "\n" + error["traceback"][-1200:]
        raise ToolError(message)
    return payload["data"]


def api(
    method: str,
    path: str,
    body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    **query: Any,
) -> Any:
    """Call the PDSO HTTP API. Open the SSH port forward if it is necessary."""
    try:
        return _request(method, path, body, query, timeout)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
        if not SSH_HOST or isinstance(error, TimeoutError):
            raise ToolError(_unreachable_message(error))
    _open_tunnel()
    try:
        return _request(method, path, body, query, timeout)
    except (urllib.error.URLError, ConnectionError) as error:
        raise ToolError(_unreachable_message(error))


def _unreachable_message(error: Exception) -> str:
    message = (
        f"Cannot reach the PDSO API at {API_URL} ({error}). Make sure that Revit "
        "is open with pyRevit and the PDSO extension, that the pyRevit routes "
        "server is enabled (pyrevit configs routes enable), and that PDSO_SSH_HOST "
        "is set if Revit is on a different computer."
    )
    if _tunnel is not None and _tunnel.poll() is not None:
        message += (
            f" The SSH port forward to {SSH_HOST} stopped (exit code "
            f"{_tunnel.returncode}): make sure that key-based SSH login works."
        )
    return message


def _clean(mapping: dict | None) -> dict:
    return {key: value for key, value in (mapping or {}).items() if value is not None}


_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{device}{number}" for device in ("COM", "LPT") for number in range(10)
}


def _image_path(name: str) -> Path:
    """Path of a PNG in OUTPUT_DIR whose file name is valid on macOS and Windows.

    Only letters, digits, "-" and "_" stay. All other characters (also the
    characters that Windows forbids: < > : " / \\ | ? *) become "_". The name is
    short, so that the full path stays below the 260-character limit of Windows.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
    safe = safe[:100] or "image"
    if safe.upper() in _WINDOWS_RESERVED:
        safe = f"_{safe}"
    return OUTPUT_DIR / f"{safe}.png"


def _save_image(name: str, data: bytes) -> Path:
    path = _image_path(name)
    path.write_bytes(data)
    return path


# =============================================================================
# Status, documents, levels
# =============================================================================


@mcp.tool()
def get_status() -> dict:
    """Get the Revit version, the open document, its levels, and if the model has a PDSO configuration. Call this first."""
    return api("GET", "status")


@mcp.tool()
def open_project(path: str) -> dict:
    """Open a Revit project file and make it the active document.

    Args:
        path: File path on the Revit computer (for example C:\\Projects\\tower.rvt).
    """
    return api("POST", "document/open", {"path": path}, timeout=LONG_TIMEOUT)


@mcp.tool()
def new_project(
    path: str, template: str | None = None, overwrite: bool = False
) -> dict:
    """Create a new Revit project file from a template, then open it.

    Args:
        path: File path on the Revit computer for the new .rvt file.
        template: Path of a .rte template. Default: the default template of Revit.
        overwrite: Replace the file if it is there.
    """
    body = _clean({"path": path, "template": template, "overwrite": overwrite})
    return api("POST", "document/new", body, timeout=LONG_TIMEOUT)


@mcp.tool()
def close_project(title: str, save: bool = False) -> dict:
    """Close an open Revit document that is not the active document.

    Args:
        title: Title of the document (the file name without ".rvt").
        save: Save the document before it closes.
    """
    return api("POST", "document/close", {"title": title, "save": save})


@mcp.tool()
def save_project() -> dict:
    """Save the active Revit document to its file."""
    return api("POST", "document/save", {}, timeout=LONG_TIMEOUT)


@mcp.tool()
def list_levels() -> list:
    """List the levels of the model (id, name, elevation in feet), lowest first."""
    return api("GET", "levels")


@mcp.tool()
def list_rooms(level: str | None = None, name: str | None = None) -> list:
    """List the placed Rooms of the model (read-only): id, number, name, level, area (SF), and the outline as [x, y] points in feet. Use the outlines as real geometry for draft_area.

    Args:
        level: Level name.
        name: Text that the Room name or number must contain.
    """
    return api("GET", "rooms", level=level, name=name)


@mcp.tool()
def create_level(name: str, elevation: float) -> dict:
    """Create a level.

    Args:
        name: Name of the new level. It must be unique.
        elevation: Elevation in feet.
    """
    return api("POST", "levels", {"name": name, "elevation": elevation})


# =============================================================================
# Setup and configuration
# =============================================================================


@mcp.tool()
def setup_model(
    levels: list[str] | None = None,
    area_scheme_mappings: dict[str, str] | None = None,
) -> dict:
    """Make the model ready for PDSO. Creates only the items that are missing: the PDSO configuration, the 20 shared parameters, the Data Silo family, the configured area schemes, and one area plan for each scheme and level. It is safe to call again.

    Args:
        levels: Names of the levels that get area plans. Default: all levels.
        area_scheme_mappings: PDSO area parameter name to the name of an area scheme of the model, for example {"PDSO - GSF": "Gross Building", "PDSO - NSF": "Rentable"}. Give this for a model that has its own area schemes and Areas (see list_area_schemes). Then no "PDSO - ..." scheme is made. The Program Volume of a program gets the shape of the smallest Area that contains the program. Default: three new schemes "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA".
    """
    body = _clean({"levels": levels, "area_scheme_mappings": area_scheme_mappings})
    return api("POST", "bootstrap", body, timeout=LONG_TIMEOUT)


@mcp.tool()
def get_config() -> dict:
    """Read the PDSO configuration of the model: Data Silo family name, UID pattern, area scheme mappings, Program Volume prefix and height, level mappings, worksets."""
    return api("GET", "config")


@mcp.tool()
def update_config(
    data_silo_family_name: str | None = None,
    uid_pattern: str | None = None,
    program_volume_family_name_prefix: str | None = None,
    program_volume_extrusion_height: float | None = None,
    area_scheme_mappings: dict[str, str] | None = None,
    levels_mapping: dict[str, str] | None = None,
    worksets: dict[str, str] | None = None,
) -> dict:
    """Change PDSO configuration fields. Fields that you do not give keep their value. A map field is replaced as a whole.

    Args:
        data_silo_family_name: Name of the family whose instances are programs.
        uid_pattern: Regular expression that each program UID must match.
        program_volume_family_name_prefix: Prefix of the Program Volume family names.
        program_volume_extrusion_height: Height of the Program Volumes in feet.
        area_scheme_mappings: PDSO area parameter name to area scheme name, for example {"PDSO - GSF": "PDSO - GSF"}.
        levels_mapping: Stacking key to level name, for example {"01": "L1", "02": "L2"}. The "stacking" field of a program uses these keys ("01-02" or "01, 03").
        worksets: Element kind to workset name.
    """
    body = _clean(
        {
            "data_silo_family_name": data_silo_family_name,
            "uid_pattern": uid_pattern,
            "program_volume_family_name_prefix": program_volume_family_name_prefix,
            "program_volume_extrusion_height": program_volume_extrusion_height,
            "area_scheme_mappings": area_scheme_mappings,
            "levels_mapping": levels_mapping,
            "worksets": worksets,
        }
    )
    return api("PUT", "config", body)


@mcp.tool()
def check_config() -> list:
    """Run the model checks of the Project Config window: schema, Data Silo family, shared parameters, area schemes, area counts."""
    return api("GET", "config/checks")


# =============================================================================
# Preflight, sync, audit
# =============================================================================


@mcp.tool()
def run_preflight() -> dict:
    """Run the 16 preflight checks that must pass before a sync. Returns can_run_sync, the error and warning counts, and each check with its level and message."""
    return api("GET", "preflight", timeout=LONG_TIMEOUT)


@mcp.tool()
def run_sync(
    update_program_volume_geometry: bool = True,
    update_program_volume_data: bool = True,
    pin_elements: bool = True,
    fix_worksets: bool = True,
    force: bool = False,
) -> dict:
    """Run the PDSO sync. It runs the preflight checks first. Phase 1 copies the area of each Area to the program in it (GSF, NSF, ZFA). Phase 2 creates or updates one Program Volume family for each program, from its GSF Area outline. Phase 3 creates, moves and updates the Program Volume instances on each level in the program's stacking. This changes the model.

    Args:
        update_program_volume_geometry: Run phase 2.
        update_program_volume_data: Run phase 3.
        pin_elements: Pin the programs, Areas and Program Volumes after the sync.
        fix_worksets: Move the elements to the configured worksets (workshared models).
        force: Run the sync also if a preflight check fails.
    """
    workset = "Fix" if fix_worksets else "Do Nothing"
    pinning = "Pin" if pin_elements else "Maintain"
    options = {
        "area_harvest_workset": workset,
        "area_harvest_pinning": pinning,
        "program_volume_geometry_update": (
            "Yes - Use Geometry Cache" if update_program_volume_geometry else "No"
        ),
        "program_volume_geometry_workset": workset,
        "program_volume_geometry_pinning": pinning,
        "program_volume_data_update": "Yes" if update_program_volume_data else "No",
    }
    return api(
        "POST", "sync", {"options": options, "force": force}, timeout=LONG_TIMEOUT
    )


@mcp.tool()
def get_audit_report() -> str:
    """Get the detailed PDSO audit report as markdown: summary, configuration, element counts, correlation of programs to Areas, level mappings, Area boundary problems, geometry tracking. Element ids are in the text as [id: N]."""
    return api("GET", "audit", timeout=LONG_TIMEOUT)["markdown"]


# =============================================================================
# Programs (room types and unit types)
# =============================================================================


@mcp.tool()
def get_program_fields() -> list:
    """List the fields of a program (a room type or unit type), the Revit parameter of each field, its type, and if you can set it. The sync writes the fields zfa, nsf, gsf and last_synced."""
    return api("GET", "programs/schema")


@mcp.tool()
def list_parameters() -> dict:
    """List the parameters of the model. "configured" is false until setup_model has run (the sync cannot run then). "types" are the permitted types of a new parameter. "parameters" has the tracked parameters first: name (Revit parameter name), field (the name to use in program fields and filters), type, role (core, area, standard, custom), tracked, editable, bound (false if the Revit parameter is missing or not on Generic Models) and problem. A built-in parameter that the model does not track has tracked false."""
    return api("GET", "parameters")


@mcp.tool()
def create_parameter(name: str, type: str = "string") -> dict:
    """Track a new program parameter for data that no field holds, for example "Tenant Type" or "Rent per SF". It becomes a Revit shared parameter on all programs and Program Volumes. The reply gives the field name (for example "rent_per_sf"): use it in update_program, update_programs, list_programs, summarize_programs and color_areas. The sync copies the value from a program to its Program Volumes. If the model has a shared parameter with the name already, that parameter is used and its values stay ("reattached": true).

    Args:
        name: Parameter name as Revit shows it, for example "Tenant Type". The name and the type cannot change later.
        type: "string", "multilineText", "number", "integer", "area" (square feet), "length" (feet) or "yesno".
    """
    return api("POST", "parameters", {"name": name, "type": type})


@mcp.tool()
def delete_parameter(parameter: str, purge: bool = False) -> dict:
    """Stop tracking a parameter. The required parameters cannot be deleted: Program UID, Program Stacking, Last Synced, and each parameter that an area scheme mapping writes into.

    Args:
        parameter: Field name or parameter name.
        purge: False: PDSO stops using the parameter, but the Revit parameter and its values stay, and create_parameter with the same name gets them back. True: also remove the parameter from Generic Models. This deletes its values on all programs and Program Volumes.
    """
    return api("DELETE", f"parameters/{urllib.parse.quote(parameter, safe='')}", purge=purge)


@mcp.tool()
def list_programs(
    level: str | None = None,
    program_type: str | None = None,
    unit_layout_type: str | None = None,
    use_group: str | None = None,
    market_segment: str | None = None,
) -> list:
    """List the programs (room types and unit types) in the model with all their fields, level and position. Give filters to get only the programs that match.

    Args:
        level: Level name.
        program_type: Value of the field program_type.
        unit_layout_type: Value of the field unit_layout_type.
        use_group: Value of the field use_group.
        market_segment: Value of the field market_segment.
    """
    return api(
        "GET",
        "programs",
        level=level,
        program_type=program_type,
        unit_layout_type=unit_layout_type,
        use_group=use_group,
        market_segment=market_segment,
    )


@mcp.tool()
def describe_program(program: str) -> dict:
    """Describe one program completely: all fields, level, position, the Area of each area scheme that contains it, and its Program Volume family and instances.

    Args:
        program: UID of the program (for example A1-01-1B) or its Revit element id.
    """
    return api("GET", f"programs/{urllib.parse.quote(str(program), safe='')}")


@mcp.tool()
def summarize_programs(group_by: str = "program_type") -> list:
    """Count the programs and add their areas (GSF, NSF, ZFA) for each value of a field.

    Args:
        group_by: Field name, for example program_type, unit_layout_type or use_group.
    """
    return api("GET", "programs/summary", group_by=group_by)


@mcp.tool()
def create_program(
    uid: str,
    level: str,
    x: float,
    y: float,
    fields: dict[str, Any] | None = None,
) -> dict:
    """Create a program (a room type or unit type): place a Data Silo at a point and set its fields. Put the point in the Area that the program is for. Call run_sync afterward to get its areas and Program Volumes.

    Args:
        uid: Unique id of the program. It must match the configured UID pattern (default: two characters, two digits, two characters, for example A1-01-1B).
        level: Level name.
        x: X coordinate in feet.
        y: Y coordinate in feet.
        fields: Other field values, for example {"display_name": "One Bedroom", "program_type": "Residential", "unit_layout_type": "1BR", "bedroom_count": 1, "stacking": "01-02"}. See get_program_fields.
    """
    body = {"level": level, "x": x, "y": y, "fields": dict(fields or {}, uid=uid)}
    return api("POST", "programs", body)


@mcp.tool()
def create_programs(items: list[dict[str, Any]]) -> dict:
    """Create many programs in one step. Use this for a model that has Areas: one item for each Area. If one item is not valid, no program is created and the error names the item. Call run_sync afterward.

    Args:
        items: One object for each program. Either {"area_id": 123, "fields": {...}}: the program goes to a point inside that Area (see list_areas). Or {"level": "L1", "x": 10.0, "y": 5.0, "fields": {...}}. "fields" must have "uid" (it must match the UID pattern and be unique) and can have other fields, also custom fields.
    """
    return api("POST", "programs/batch", {"items": items}, timeout=LONG_TIMEOUT)


@mcp.tool()
def update_program(
    program: str,
    fields: dict[str, Any] | None = None,
    x: float | None = None,
    y: float | None = None,
) -> dict:
    """Change the fields and the position of one program.

    Args:
        program: UID of the program or its Revit element id.
        fields: Field values to change, for example {"display_name": "Studio", "bedroom_count": 0}.
        x: New X coordinate in feet.
        y: New Y coordinate in feet.
    """
    body = _clean({"fields": fields, "x": x, "y": y})
    return api("PUT", f"programs/{urllib.parse.quote(str(program), safe='')}", body)


@mcp.tool()
def update_programs(filters: dict[str, Any], fields: dict[str, Any]) -> dict:
    """Set the same field values on all programs that match the filters.

    Args:
        filters: Field name to value, for example {"program_type": "Residential"}. "level" filters by level name.
        fields: Field values to set, for example {"market_segment": "Affordable"}.
    """
    return api("PUT", "programs", {"filters": filters, "fields": fields})


@mcp.tool()
def delete_program(program: str) -> dict:
    """Delete one program (its Data Silo). Its Program Volumes stay until the next sync.

    Args:
        program: UID of the program or its Revit element id.
    """
    return api("DELETE", f"programs/{urllib.parse.quote(str(program), safe='')}")


# =============================================================================
# Selection
# =============================================================================


@mcp.tool()
def select_elements(element_ids: list[int], zoom: bool = True) -> dict:
    """Select elements in the Revit window (programs, Areas, Program Volumes or other elements) and zoom to them.

    Args:
        element_ids: Revit element ids.
        zoom: Zoom the active view to the elements.
    """
    return api("POST", "selection", {"ids": element_ids, "zoom": zoom})


@mcp.tool()
def select_programs(
    level: str | None = None,
    program_type: str | None = None,
    unit_layout_type: str | None = None,
    uids: list[str] | None = None,
) -> dict:
    """Select programs in the Revit window by filter or by UID.

    Args:
        level: Level name.
        program_type: Value of the field program_type.
        unit_layout_type: Value of the field unit_layout_type.
        uids: UIDs of the programs to select.
    """
    programs = api(
        "GET",
        "programs",
        level=level,
        program_type=program_type,
        unit_layout_type=unit_layout_type,
    )
    if uids is not None:
        programs = [program for program in programs if program["uid"] in uids]
    if not programs:
        raise ToolError("No program matches.")
    result = api("POST", "selection", {"ids": [program["id"] for program in programs]})
    result["uids"] = [program["uid"] for program in programs]
    return result


@mcp.tool()
def get_selection() -> list:
    """Describe the elements that are selected in the Revit window now. Each element has a kind: program, area or other."""
    return api("GET", "selection")


# =============================================================================
# Areas and outlines
# =============================================================================


@mcp.tool()
def list_area_schemes() -> list:
    """List the area schemes, the number of Areas in each one, and the PDSO parameter that each one feeds."""
    return api("GET", "area-schemes")


@mcp.tool()
def list_areas(
    scheme: str | None = None,
    level: str | None = None,
    include_outline: bool = True,
) -> list:
    """List the Areas: id, number, name, scheme, level, area (SF), perimeter, and the outline as [x, y] points in feet.

    Args:
        scheme: Area scheme name, for example "PDSO - GSF".
        level: Level name.
        include_outline: Include the outline points.
    """
    return api(
        "GET",
        "areas",
        scheme=scheme,
        level=level,
        outline="true" if include_outline else "false",
    )


@mcp.tool()
def draft_area(
    scheme: str,
    level: str,
    points: list[list[float]],
    name: str | None = None,
    number: str | None = None,
) -> dict:
    """Draft a new Area: draw its outline as area boundary lines and place the Area in it. Edges that are on boundary lines already in the plan are not drawn again, so an Area can share edges with its neighbors. Returns the Area with the area that Revit computed. Use render_plan with draft_outlines first to preview an outline.

    Args:
        scheme: Area scheme name, for example "PDSO - GSF".
        level: Level name.
        points: Outline as [[x, y], ...] in feet, in order, at least 3 points. Do not repeat the first point.
        name: Name of the Area.
        number: Number of the Area.
    """
    body = _clean(
        {
            "scheme": scheme,
            "level": level,
            "points": points,
            "name": name,
            "number": number,
        }
    )
    return api("POST", "areas", body)


@mcp.tool()
def draft_area_from_room(
    scheme: str,
    level: str,
    room_number: str,
    inset: float = 0.0,
    tolerance: float = 0.05,
) -> dict:
    """Draft a new Area from the boundary of a placed Room. Reads the Room outline, removes the very short segments, optionally moves the outline to the inside, then drafts the Area (same as draft_area). The Area gets the name and number of the Room. Returns the Area with the area that Revit computed, and program_point: a point inside the Area where a program (create_program) can go.

    Args:
        scheme: Area scheme name, for example "PDSO - GSF".
        level: Level name of the Room.
        room_number: Number of the Room, for example "303".
        inset: Distance in feet to move the outline to the inside (0 keeps the Room boundary).
        tolerance: Simplify tolerance in feet. Outline points that change the shape less than this are removed.
    """
    from shapely.geometry import Polygon

    rooms = [
        room
        for room in api("GET", "rooms", level=level)
        if str(room["number"]) == str(room_number)
    ]
    if not rooms:
        raise ToolError(f"No placed Room with number '{room_number}' on level '{level}'.")
    room = rooms[0]
    if len(room["outline"]) < 3:
        raise ToolError(f"Room {room_number} has no closed outline.")
    polygon = Polygon(room["outline"]).simplify(tolerance, preserve_topology=True)
    if inset:
        polygon = polygon.buffer(-abs(inset), join_style=2).simplify(
            min(tolerance, 0.02), preserve_topology=True
        )
    if polygon.is_empty or polygon.geom_type != "Polygon":
        raise ToolError(
            f"The outline of Room {room_number} with inset {inset} ft is not one closed shape."
        )
    points = [[round(x, 3), round(y, 3)] for x, y in list(polygon.exterior.coords)[:-1]]
    body = {
        "scheme": scheme,
        "level": level,
        "points": points,
        "name": room["name"],
        "number": str(room["number"]),
    }
    area = api("POST", "areas", body)
    inner = polygon.buffer(-0.5, join_style=2)
    point = (inner if not inner.is_empty else polygon).representative_point()
    result = {key: value for key, value in area.items() if key not in ("outline", "holes")}
    result["room"] = {
        "number": str(room["number"]),
        "name": room["name"],
        "room_area": room["area"],
        "room_outline_points": len(room["outline"]),
    }
    result["outline_points"] = len(points)
    result["inset"] = inset
    result["program_point"] = {"x": round(point.x, 2), "y": round(point.y, 2)}
    return result


@mcp.tool()
def adjust_area(
    area_id: int,
    points: list[list[float]] | None = None,
    move_dx: float | None = None,
    move_dy: float | None = None,
    vertex_index: int | None = None,
    vertex_x: float | None = None,
    vertex_y: float | None = None,
    scale: float | None = None,
    name: str | None = None,
    number: str | None = None,
) -> dict:
    """Adjust one Area. Give one geometry change: a new outline (points), a move (move_dx and move_dy), one moved outline point (vertex_index, vertex_x, vertex_y), or a scale factor. You can also change the name and number. Returns the Area after the change, with previous_area and the new area. If Revit cannot enclose the Area with the new outline, no change is made.

    Args:
        area_id: Revit element id of the Area.
        points: New outline as [[x, y], ...] in feet.
        move_dx: Move along X in feet. The programs in the Area move with it.
        move_dy: Move along Y in feet.
        vertex_index: Index of the outline point to move (0 is the first point of the outline that list_areas gives).
        vertex_x: New X of that point in feet.
        vertex_y: New Y of that point in feet.
        scale: Linear scale factor about the centroid of the outline (2.0 gives four times the area).
        name: New name of the Area.
        number: New number of the Area.
    """
    body: dict[str, Any] = _clean(
        {"points": points, "scale": scale, "name": name, "number": number}
    )
    if move_dx is not None or move_dy is not None:
        body["move"] = {"dx": move_dx or 0.0, "dy": move_dy or 0.0}
    if vertex_index is not None:
        if vertex_x is None or vertex_y is None:
            raise ToolError("Give vertex_x and vertex_y with vertex_index.")
        body["vertex"] = {"index": vertex_index, "x": vertex_x, "y": vertex_y}
    return api("PUT", f"areas/{int(area_id)}", body)


@mcp.tool()
def delete_area(area_id: int, delete_outline: bool = True) -> dict:
    """Delete one Area.

    Args:
        area_id: Revit element id of the Area.
        delete_outline: Also delete the boundary lines that only this Area uses.
    """
    return api(
        "DELETE",
        f"areas/{int(area_id)}",
        delete_outline="true" if delete_outline else "false",
    )


# =============================================================================
# Visualization
# =============================================================================


@mcp.tool()
def list_views(view_type: str | None = None) -> list:
    """List the views of the model that can be shown or exported.

    Args:
        view_type: Revit view type, for example AreaPlan, FloorPlan or ThreeD.
    """
    return api("GET", "views", type=view_type)


@mcp.tool()
def show_view(view: str, zoom_box: list[float] | None = None) -> dict:
    """Show a view in the Revit window.

    Args:
        view: View name or element id.
        zoom_box: Zoom a plan view to this rectangle: [x_min, y_min, x_max, y_max] in feet.
    """
    return api("POST", "views/activate", _clean({"view": view, "zoom_box": zoom_box}))


@mcp.tool()
def color_areas(view: str, color_by: str = "program_type", labels: bool = True) -> dict:
    """Color the Areas of an area plan in Revit by a program field. Draws one filled region and one label (name, UID, value, SF) for each Area, and crops the view to the Areas. Returns the legend. Use export_view_image to see the result.

    Args:
        view: Name or element id of an area plan view, for example "PDSO - GSF - L1".
        color_by: Program field that gives the color, for example program_type or unit_layout_type.
        labels: Also write the labels.
    """
    return api(
        "POST",
        "views/color-areas",
        {"view": view, "color_by": color_by, "labels": labels},
    )


@mcp.tool()
def clear_area_colors(view: str) -> dict:
    """Remove the color overlay that color_areas made from a view.

    Args:
        view: Name or element id of the view.
    """
    return api("POST", "views/clear-colors", {"view": view})


@mcp.tool()
def export_view_image(view: str | None = None, pixel_width: int = 1600) -> list:
    """Export a Revit view to a PNG image and return the image. This is the view as Revit draws it.

    Args:
        view: View name or element id. Default: the active view.
        pixel_width: Width of the image in pixels (200 to 6000).
    """
    result = api(
        "POST",
        "views/export",
        _clean({"view": view, "pixel_width": pixel_width}),
        timeout=LONG_TIMEOUT,
    )
    data = base64.b64decode(result["image_base64"])
    path = _save_image(f"view_{result['view']['name']}", data)
    return [
        f"View '{result['view']['name']}' saved to {path}",
        Image(data=data, format="png"),
    ]


@mcp.tool()
def show_program_volumes(
    color_by: str = "program_type",
    pixel_width: int = 1600,
    building: str = "ghost",
    section_box: bool = True,
) -> list:
    """Make (or update) the shaded 3D view "PDSO - Program Volumes", color the Program Volumes by a program field, and return its image and legend. Program Volumes come from run_sync.

    Args:
        color_by: Program field that gives the color.
        pixel_width: Width of the image in pixels.
        building: "ghost" makes the other model elements transparent, so that the volumes in a building show. "show" keeps them as they are.
        section_box: Cut the view to a box around the Program Volumes.
    """
    info = api(
        "POST",
        "views/program-volumes",
        {"color_by": color_by, "building": building, "section_box": section_box},
        timeout=LONG_TIMEOUT,
    )
    result = api(
        "POST",
        "views/export",
        {"view": info["view"]["id"], "pixel_width": pixel_width},
        timeout=LONG_TIMEOUT,
    )
    data = base64.b64decode(result["image_base64"])
    path = _save_image("program_volumes", data)
    summary = {"view": info["view"], "legend": info["legend"], "file": str(path)}
    return [json.dumps(summary), Image(data=data, format="png")]


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
NO_PROGRAM = "(no program)"


def _font(size: int) -> ImageFont.FreeTypeFont:
    windows_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    for name in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        str(windows_fonts / "arial.ttf"),
        str(windows_fonts / "segoeui.ttf"),
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    # Scalable font that is in Pillow 10.1 and later. Like the fonts above it
    # has .size, which render_plan uses (the old bitmap default font has not).
    return ImageFont.load_default(size)


def _inside(x: float, y: float, polygon: list[list[float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _centroid(polygon: list[list[float]]) -> tuple[float, float]:
    area = cx = cy = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-9:
        return polygon[0][0], polygon[0][1]
    return cx / (3 * area), cy / (3 * area)


def _shoelace(polygon: list[list[float]]) -> float:
    total = 0.0
    for i in range(len(polygon)):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


@mcp.tool()
def render_plan(
    level: str,
    scheme: str = "PDSO - GSF",
    color_by: str = "program_type",
    draft_outlines: list[list[list[float]]] | None = None,
    pixel_width: int = 1600,
) -> list:
    """Draw a plan diagram of one level from the live model data: the Area outlines of one area scheme, filled by a program field, with labels, program positions, outline point indexes, a scale bar and a legend. This tool does not change the model. Use it to see the program quickly, to find vertex indexes for adjust_area, and to preview new outlines before draft_area.

    Args:
        level: Level name.
        scheme: Area scheme name.
        color_by: Program field that gives the fill color.
        draft_outlines: Outlines to preview, each as [[x, y], ...] in feet. They are drawn dashed with their computed area. They are not created in Revit.
        pixel_width: Width of the image in pixels.
    """
    areas = [
        a
        for a in api("GET", "areas", scheme=scheme, level=level)
        if len(a["outline"]) >= 3
    ]
    try:
        programs = [p for p in api("GET", "programs", level=level) if p["location"]]
    except ToolError:
        programs = []  # the model has no PDSO configuration: draw the outlines only
    drafts = [d for d in (draft_outlines or []) if len(d) >= 3]

    points = [p for a in areas for p in a["outline"]] + [p for d in drafts for p in d]
    if not points:
        raise ToolError(
            f"No Areas in scheme '{scheme}' on level '{level}', and no draft outlines."
        )
    min_x = min(p[0] for p in points)
    max_x = max(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_y = max(p[1] for p in points)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)

    margin = 70
    legend_width = 330
    plot_width = pixel_width - legend_width - 2 * margin
    scale = plot_width / span_x
    plot_height = span_y * scale
    height = int(max(plot_height + 2 * margin + 60, 420))
    image = PILImage.new("RGB", (pixel_width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")

    def to_px(point: list[float]) -> tuple[float, float]:
        return (
            margin + (point[0] - min_x) * scale,
            margin + 40 + (max_y - point[1]) * scale,
        )

    title_font = _font(26)
    font = _font(max(12, min(22, int(scale * 1.4))))
    small = _font(15)
    draw.text(
        (margin, 18),
        f"{scheme}  |  {level}  |  color: {color_by}",
        fill="black",
        font=title_font,
    )

    colors: dict[str, tuple[int, int, int]] = {}
    legend: dict[str, dict] = {}
    for area in areas:
        program = next(
            (
                p
                for p in programs
                if _inside(p["location"]["x"], p["location"]["y"], area["outline"])
            ),
            None,
        )
        if program is None:
            value, color = NO_PROGRAM, (205, 205, 205)
        else:
            raw = program.get(color_by)
            value = str(raw) if raw not in (None, "") else "(blank)"
            color = colors.setdefault(value, PALETTE[len(colors) % len(PALETTE)])
        entry = legend.setdefault(value, {"color": color, "count": 0, "area": 0.0})
        entry["count"] += 1
        entry["area"] += area["area"]

        polygon = [to_px(p) for p in area["outline"]]
        draw.polygon(polygon, fill=color + (150,))
        for hole in area.get("holes", []):
            if len(hole) >= 3:
                draw.polygon([to_px(p) for p in hole], fill=(255, 255, 255, 255))
        draw.line(polygon + [polygon[0]], fill=(30, 30, 30, 255), width=3)
        # Put each point index inside its own Area, so that the indexes of two
        # Areas that share a corner do not cover each other.
        center = to_px(list(_centroid(area["outline"])))
        for index, pixel in enumerate(polygon):
            draw.ellipse(
                (pixel[0] - 4, pixel[1] - 4, pixel[0] + 4, pixel[1] + 4),
                fill=(30, 30, 30, 255),
            )
            dx, dy = center[0] - pixel[0], center[1] - pixel[1]
            length = max((dx * dx + dy * dy) ** 0.5, 1.0)
            draw.text(
                (pixel[0] + dx / length * 20 - 5, pixel[1] + dy / length * 20 - 8),
                str(index),
                fill=(70, 70, 70, 255),
                font=small,
            )
        if program is not None:
            px, py = to_px([program["location"]["x"], program["location"]["y"]])
            draw.ellipse(
                (px - 7, py - 7, px + 7, py + 7), outline=(60, 60, 60, 255), width=2
            )

        lines = [area["name"] or area["number"] or f"Area {area['id']}"]
        if program is not None:
            lines.append(program["uid"] or "")
            lines.append(value)
        lines.append(f"{area['area']:,.0f} SF  (id {area['id']})")
        lines = [line for line in lines if line]
        cx, cy = to_px(list(_centroid(area["outline"])))
        line_height = font.size + 4
        block_height = line_height * len(lines)
        top = cy - block_height / 2
        if program is not None:
            # Keep the label clear of the program marker
            px, py = to_px([program["location"]["x"], program["location"]["y"]])
            block_width = max(draw.textlength(line, font=font) for line in lines)
            if (
                abs(px - cx) < block_width / 2 + 10
                and top - 10 < py < top + block_height + 10
            ):
                min_py = min(pixel[1] for pixel in polygon)
                above = py - 12 - block_height
                top = above if above > min_py + 4 else py + 12
        for number, line in enumerate(lines):
            width = draw.textlength(line, font=font)
            draw.text(
                (cx - width / 2, top + number * line_height),
                line,
                fill="black",
                font=font,
            )

    drawn = set()
    for area in areas:
        for program in programs:
            if _inside(
                program["location"]["x"], program["location"]["y"], area["outline"]
            ):
                drawn.add(program["id"])
    for program in programs:
        if program["id"] in drawn:
            continue  # programs outside all Areas: show them in red
        px, py = to_px([program["location"]["x"], program["location"]["y"]])
        draw.ellipse(
            (px - 7, py - 7, px + 7, py + 7), outline=(200, 0, 0, 255), width=3
        )
        draw.text(
            (px + 10, py - 8), program["uid"] or "", fill=(200, 0, 0, 255), font=small
        )

    for number, outline in enumerate(drafts):
        polygon = [to_px(p) for p in outline]
        loop = polygon + [polygon[0]]
        for start, end in zip(loop, loop[1:]):
            length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
            steps = max(int(length / 14), 1)
            for step in range(0, steps, 2):
                a = step / steps
                b = min((step + 1) / steps, 1.0)
                draw.line(
                    (
                        start[0] + (end[0] - start[0]) * a,
                        start[1] + (end[1] - start[1]) * a,
                        start[0] + (end[0] - start[0]) * b,
                        start[1] + (end[1] - start[1]) * b,
                    ),
                    fill=(200, 0, 120, 255),
                    width=4,
                )
        cx, cy = to_px(list(_centroid(outline)))
        text = f"DRAFT {number + 1}: {_shoelace(outline):,.0f} SF"
        draw.text(
            (cx - draw.textlength(text, font=font) / 2, cy - font.size / 2),
            text,
            fill=(200, 0, 120, 255),
            font=font,
        )

    bar_feet = next(
        (b for b in (100, 50, 20, 10, 5, 2, 1) if b * scale <= plot_width / 3), 1
    )
    bar_y = height - 34
    draw.line((margin, bar_y, margin + bar_feet * scale, bar_y), fill="black", width=4)
    draw.text(
        (margin + bar_feet * scale + 10, bar_y - 10),
        f"{bar_feet} ft",
        fill="black",
        font=small,
    )
    draw.text(
        (margin + bar_feet * scale + 90, bar_y - 10),
        f"X {min_x:.1f} to {max_x:.1f} ft,  Y {min_y:.1f} to {max_y:.1f} ft.  Small numbers: outline point indexes.  Circles: programs (red: not in an Area).",
        fill=(90, 90, 90),
        font=small,
    )

    legend_x = pixel_width - legend_width
    draw.text((legend_x, margin + 30), "Legend", fill="black", font=title_font)
    row_y = margin + 76
    for value in sorted(legend):
        entry = legend[value]
        draw.rectangle(
            (legend_x, row_y, legend_x + 26, row_y + 26),
            fill=entry["color"] + (150,),
            outline=(30, 30, 30, 255),
        )
        draw.text((legend_x + 36, row_y - 2), value, fill="black", font=small)
        draw.text(
            (legend_x + 36, row_y + 16),
            f"{entry['count']} area(s), {entry['area']:,.0f} SF",
            fill=(90, 90, 90),
            font=small,
        )
        row_y += 50

    path = _image_path(f"plan_{level}_{scheme}")
    image.save(path)
    summary = {
        "file": str(path),
        "areas": len(areas),
        "programs": len(programs),
        "legend": [
            {
                "value": value,
                "count": legend[value]["count"],
                "area": legend[value]["area"],
            }
            for value in sorted(legend)
        ],
        "drafts": [
            {"draft": i + 1, "area": _shoelace(d)} for i, d in enumerate(drafts)
        ],
    }
    return [json.dumps(summary), Image(data=path.read_bytes(), format="png")]


if __name__ == "__main__":
    mcp.run()
