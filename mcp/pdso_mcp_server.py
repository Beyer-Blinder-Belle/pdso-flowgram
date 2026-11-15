# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2,<3", "pillow>=10.1", "shapely>=2"]
# ///
"""MCP server for PDSO Flowgram.

This server runs on your computer (macOS, Windows or Linux). It sends each tool
call to the PDSO HTTP API in Revit (see startup.py at the repository root). Revit can be
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
import math
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
RELOAD_WAIT = 60  # seconds to wait for the routes during a pyRevit reload
# Version of the HTTP API (API_VERSION in lib/pdso/code/api/handlers.py)
# that this server was written for.
EXPECTED_API_VERSION = "1.5"

# An empty ProxyHandler turns off all proxies. Without it, urllib reads
# HTTP_PROXY and, on Windows, the system proxy in the registry, and can send the
# 127.0.0.1 API calls to the proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

mcp = MCPServer(
    "pdso",
    instructions=(
        "Tools for the PDSO Flowgram Revit plugin: programs (room types and unit "
        "types, stored as Data Silos), Area outlines, Program Volumes, sync and "
        "audit. All lengths are decimal feet and all areas are square feet.\n"
        "The user does not need to know PDSO. Work from the user's intent and "
        "decide the PDSO steps yourself, but keep the user in the loop for "
        "setup: it adds items to their model.\n"
        "How PDSO works: a program is a point object (Data Silo) in a 2D Area. "
        "Each program needs one Area in each configured area scheme (default: "
        "PDSO - GSF gross, PDSO - ZFA zoning, PDSO - NSF net). The sync copies the "
        "area of each Area to its program and builds one 3D Program Volume for "
        "the program on each level of its stacking.\n"
        "Setting up a model: call get_setup_status, then tell the user in plain "
        "words what a setup would add and what the model has already, and agree "
        "the scope before you call setup_model, unless they already said what "
        "they want. The full package (the default) is what the PDSO ribbon's "
        "setup buttons make: the configuration, the 20 built-in parameters, "
        "area schemes, the Data Silo family, view templates, area plans for "
        "each scheme and level, a 3D view and two schedules. The minimum to "
        "place programs and run the sync is parts=[\"area_schemes\", "
        "\"families\"] with parameters=\"required\". Offer both, or a scope in "
        "between; the user may not want views or schedules added to a working "
        "model. A smaller scope is fine: setup_model can add parts later.\n"
        "Order of work for a model that is new to PDSO: (1) get_status. (2) "
        "get_setup_status, agree the scope, then setup_model one time. (3) "
        "update_config with levels_mapping: one "
        "two-digit stacking key for each occupied level, lowest first (01, 02, "
        "...). (4) list_rooms(level, include_outline=false) to see the Rooms. (5) "
        "draft_areas_from_rooms for the Rooms that fit the user's intent, with "
        "one inset for each scheme. (6) create_programs with the area_id of each "
        "Area of the scheme that feeds PDSO - GSF, or create_program at the "
        "program_point. (7) run_preflight, then run_sync. (8) color_areas, "
        "show_program_volumes, summarize_programs, get_audit_report.\n"
        "Rules: call run_sync again after each change of programs or Areas, "
        "before you show results or totals. Program UID: it must match the "
        "uid_pattern of get_config (default: two characters, two digits, two "
        "characters, for example CL-02-18) and be unique. Stacking: the stacking "
        "keys of the levels where the program repeats (\"02\", \"01-03\", \"01, "
        "03\"); default is the level of the program only. show_view(view, "
        "fit=true) shows a plan and zooms it to its Areas or Rooms. If two views "
        "have the same name, give view_type or the view id. Do not select "
        "elements in a view after color_areas.\n"
        "When the user describes data that no program field holds, call "
        "create_parameter, then set the new field with update_program or "
        "update_programs. In a model that has its own Areas and area schemes, "
        "give the scheme names to setup_model (area_scheme_mappings) and place "
        "the programs with create_programs."
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


# Title of the open Revit document that the tools work on. None: the active document.
_target_document: str | None = None

# Requests that change nothing in the model. It is safe to send them again.
_READ_ONLY_POSTS = (
    "views/export",
    "views/activate",
    "catalogue/recommend",
    "catalogue/similar-rooms",
)


def _request(method: str, path: str, body: Any, query: dict, timeout: int) -> Any:
    """Send one request. Send it again if the reply belongs to a different request."""
    if (
        _target_document
        and query.get("document") is None
        and not path.startswith("documents")
    ):
        query = dict(query, document=_target_document)
    read_only = method == "GET" or path in _READ_ONLY_POSTS
    for attempt in range(3):
        payload, request_id = _send(method, path, body, query, timeout)
        if payload.get("rid") in (None, request_id):
            break
        # pyRevit Routes gave this client the reply of a different request.
        if not read_only:
            raise ToolError(
                "The reply from Revit belongs to a different request (more than one "
                "client uses this Revit at the same time). The change of this call "
                "was possibly made. Read the model state before you send the call again."
            )
        time.sleep(0.5)
    else:
        raise ToolError(
            "The replies from Revit belong to different requests (more than one "
            "client uses this Revit at the same time). Try again later."
        )
    if not payload.get("ok"):
        error = payload.get("error", {})
        message = error.get("message", "Unknown error")
        if error.get("type") not in (None, "ApiError") and error.get("traceback"):
            message += "\n" + error["traceback"][-1200:]
        raise ToolError(message)
    return payload["data"]


def _send(method: str, path: str, body: Any, query: dict, timeout: int) -> tuple[dict, str]:
    request_id = uuid.uuid4().hex
    query = {key: value for key, value in query.items() if value is not None}
    query["_rid"] = request_id
    url = f"{API_URL}/{path}?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    deadline = time.monotonic() + RELOAD_WAIT
    while True:
        request = urllib.request.Request(
            url, data=data, method=method, headers=headers
        )
        try:
            raw = _OPENER.open(request, timeout=timeout).read()
        except urllib.error.HTTPError as error:
            raw = error.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ToolError(
                f"The PDSO API gave a reply that is not JSON: {raw[:300]!r}"
            )
        if not (isinstance(payload, dict) and "exception" in payload):
            break
        message = str(payload["exception"].get("message"))
        # While pyRevit reloads, the routes server answers but the PDSO routes
        # are not registered yet ("Route does not exits"). The request did not
        # reach the PDSO code, so it is safe to send it again.
        if "RouteHandlerNotDefined" in message and time.monotonic() < deadline:
            time.sleep(2)
            continue
        raise ToolError(f"pyRevit Routes error: {message}")
    if not isinstance(payload, dict):
        raise ToolError(f"The PDSO API gave a reply that is not an object: {raw[:300]!r}")
    return payload, request_id


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
    """Get the Revit version, the open document, its levels, and if the model has a PDSO configuration. Call this first. If the reply has "version_warning", the PDSO extension in Revit and this MCP server are not the same version: tell the user."""
    status = api("GET", "status")
    found = status.get("api_version")
    if found != EXPECTED_API_VERSION:
        status["version_warning"] = (
            f"The PDSO extension in Revit has API version {found or 'unknown (older than 1.1)'}. "
            f"This MCP server expects {EXPECTED_API_VERSION}. Some tools can fail. "
            "Update the extension on the Revit computer and reload pyRevit, or update this server."
        )
    return status


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
def list_rooms(
    level: str | None = None,
    name: str | None = None,
    include_outline: bool = True,
) -> list:
    """List the placed Rooms of the model (read-only): id, number, name, level, area (SF), bbox ([x_min, y_min, x_max, y_max] in feet), and the outline as [x, y] points in feet. To select Rooms or to draft Areas with draft_areas_from_rooms, the outlines are not necessary: give include_outline=false.

    Args:
        level: Level name.
        name: Text that the Room name or number must contain.
        include_outline: Include the outline points of each Room.
    """
    return api(
        "GET",
        "rooms",
        level=level,
        name=name,
        outline="true" if include_outline else "false",
    )


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
def get_setup_status() -> dict:
    """Read-only. What each part of a PDSO setup makes, whether the sync needs it, and what the model has already ("state": present, partial or missing, with details). Also "full_package" and "minimum": setup_model arguments. Call it before setup_model and use it to agree the scope with the user."""
    return api("GET", "setup")


@mcp.tool()
def setup_model(
    levels: list[str] | None = None,
    area_scheme_mappings: dict[str, str] | None = None,
    parts: list[str] | None = None,
    parameters: str = "all",
) -> dict:
    """Make the model ready for PDSO, the same way as the ribbon's three setup buttons: the PDSO configuration, the parameters, then the chosen Quick Setup parts from the model's standards files. This adds items to the user's model: agree the scope with the user first (see get_setup_status), unless they already said what they want. Items that are there are left alone. It is safe to call again, for example to add parts later. The reply has "setup": the Quick Setup report rows (step, item, status, message); look at rows with status "Failed" or "Warning".

    Args:
        levels: Names of the levels that get area plans. Default: the levels that have Rooms (all levels if the model has no Rooms).
        area_scheme_mappings: PDSO area parameter name to the name of an area scheme of the model, for example {"PDSO - GSF": "Gross Building", "PDSO - NSF": "Rentable"}. Give this for a model that has its own area schemes and Areas (see list_area_schemes). Then no "PDSO - ..." scheme is made. The Program Volume of a program gets the shape of the smallest Area that contains the program. Default: three new schemes "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA".
        parts: Quick Setup parts to run. "area_schemes": the configured area schemes. "families": the Data Silo family. "view_templates": PDSO view templates (the area plan template hides Program Volumes). "views": one area plan for each scheme and level (named like "PDSO - GSF - L1") and a 3D Program Volumes view. "schedules": Data Silo and Program Volume schedules. Default: all of them (the full package). The sync needs only "area_schemes" and "families"; [] runs only the configuration and parameters.
        parameters: Used only if the model tracks no parameters yet. "all": the 20 built-in parameters (unit data such as bedroom, bathroom and kitchen counts, use group, load factor). "required": only Program UID, Program Stacking, Last Synced and the area parameters; add others later with create_parameter. With "required", the schedules leave out the columns of untracked parameters and report that in "Warning" rows; that is expected, not an error.
    """
    if not levels:
        with_rooms = {room["level"] for room in api("GET", "rooms", outline="false")}
        levels = [
            level["name"]
            for level in api("GET", "levels")
            if level["name"] in with_rooms
        ] or None
    body = _clean(
        {
            "levels": levels,
            "area_scheme_mappings": area_scheme_mappings,
            "parts": parts,
            "parameters": parameters,
        }
    )
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
    """Run the PDSO sync. It runs the preflight checks first. Phase 1 copies the area of each Area to the program in it (GSF, NSF, ZFA). Phase 2 creates or updates one Program Volume family for each program, from its GSF Area outline. Phase 3 creates, moves and updates the Program Volume instances on each level in the program's stacking. This changes the model. The reply has "program_volumes": the count of families and instances, and for each program its Program Volume family and the id and level of each instance.

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
        name: Parameter name, for example "Tenant Type". PDSO adds the "PDSO - " prefix, as the Parameter Manager does ("PDSO - Tenant Type"), unless the model already has a shared parameter with the name as given. The name and the type cannot change later.
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
    return api(
        "DELETE", f"parameters/{urllib.parse.quote(parameter, safe='')}", purge=purge
    )


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
    """Select programs in the Revit window by filter or by UID. Do not call this when the active view has the color overlay of color_areas: the programs are under the colored regions, and Revit can show a dialog that blocks all tools until a person closes it. Call clear_area_colors first, or select in a different view.

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


# Simplify tolerances (feet) to try when an inset splits a Room outline.
_INSET_TOLERANCES = (0.3, 0.8, 1.5)


def _room_polygon(room: dict, inset: float, tolerance: float):
    """Make the Area outline of a Room: simplify, then move to the inside.

    A Room with many small wall offsets (window bays, columns) can break into
    more than one piece when the outline moves to the inside. Then a larger
    simplify tolerance is used. If the outline still has more than one piece,
    the largest piece is used. Returns (polygon, warnings).
    """
    from shapely.geometry import Polygon

    number = room["number"]
    if len(room.get("outline") or []) < 3:
        raise ToolError(f"Room {number} has no closed outline.")
    source = Polygon(room["outline"])
    if not source.is_valid:
        source = source.buffer(0)
    warnings: list[str] = []

    def build(simplify_tolerance: float):
        shape = source.simplify(simplify_tolerance, preserve_topology=True)
        if inset:
            shape = shape.buffer(-abs(inset), join_style=2).simplify(
                min(simplify_tolerance, 0.02), preserve_topology=True
            )
        return shape

    polygon = build(tolerance)
    if polygon.geom_type != "Polygon" or polygon.is_empty:
        for larger in _INSET_TOLERANCES:
            if larger <= tolerance:
                continue
            candidate = build(larger)
            if candidate.geom_type == "Polygon" and not candidate.is_empty:
                polygon = candidate
                warnings.append(
                    f"Room {number}: the outline with inset {inset} ft was not one closed shape at tolerance {tolerance} ft. Tolerance {larger} ft was used."
                )
                break
    if polygon.geom_type == "MultiPolygon" and not polygon.is_empty:
        parts = sorted(polygon.geoms, key=lambda part: part.area, reverse=True)
        lost = sum(part.area for part in parts[1:])
        polygon = parts[0]
        warnings.append(
            f"Room {number}: the outline with inset {inset} ft has {len(parts)} pieces. The largest piece was used ({lost:.1f} SF in the other pieces is not in the Area)."
        )
    if polygon.is_empty or polygon.geom_type != "Polygon":
        raise ToolError(
            f"The outline of Room {number} with inset {inset} ft is not one closed shape."
        )
    return polygon, warnings


def _draft_from_room(scheme: str, level: str, room: dict, inset: float, tolerance: float) -> dict:
    polygon, warnings = _room_polygon(room, inset, tolerance)
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
    result["warnings"] = list(result.get("warnings") or []) + warnings
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
def draft_area_from_room(
    scheme: str,
    level: str,
    room_number: str,
    inset: float = 0.0,
    tolerance: float = 0.05,
) -> dict:
    """Draft a new Area from the boundary of a placed Room. Reads the Room outline, removes the very short segments, optionally moves the outline to the inside, then drafts the Area (same as draft_area). The Area gets the name and number of the Room. Returns the Area with the area that Revit computed, and program_point: a point inside the Area where a program (create_program) can go. If the inset splits the outline, a larger tolerance or the largest piece is used and "warnings" says so. For more than one Room, use draft_areas_from_rooms.

    Args:
        scheme: Area scheme name, for example "PDSO - GSF".
        level: Level name of the Room.
        room_number: Number of the Room, for example "303".
        inset: Distance in feet to move the outline to the inside (0 keeps the Room boundary).
        tolerance: Simplify tolerance in feet. Outline points that change the shape less than this are removed.
    """
    rooms = [
        room
        for room in api("GET", "rooms", level=level)
        if str(room["number"]) == str(room_number)
    ]
    if not rooms:
        raise ToolError(f"No placed Room with number '{room_number}' on level '{level}'.")
    return _draft_from_room(scheme, level, rooms[0], inset, tolerance)


@mcp.tool()
def draft_areas_from_rooms(
    level: str,
    room_numbers: list[str],
    insets: dict[str, float] | None = None,
    tolerance: float = 0.05,
) -> dict:
    """Draft the Areas of many Rooms in one step: one Area for each Room in each area scheme. This is the fast way to map out the program of a floor. Each Area gets the name and number of its Room. A Room that fails does not stop the other Rooms: see "errors".

    Returns "areas": for each Area its scheme, number, name, id, area (SF), warnings and program_point (a point inside the Area for create_program); "created": the count for each scheme; "errors": the Rooms that failed, with the reason.

    Args:
        level: Level name of the Rooms.
        room_numbers: Numbers of the Rooms, for example ["216", "218", "219"].
        insets: Area scheme name to the distance in feet that the outline moves to the inside of the Room boundary (0 keeps the Room boundary). One Area is drafted in each scheme that you give, for example {"PDSO - GSF": 0, "PDSO - ZFA": 0, "PDSO - NSF": 0.25}. Default: all area schemes of the PDSO configuration, with inset 0.
        tolerance: Simplify tolerance in feet.
    """
    if not room_numbers:
        raise ToolError("Give at least one Room number.")
    if insets is None:
        mappings = api("GET", "config").get("area_scheme_mappings") or {}
        insets = {scheme: 0.0 for scheme in mappings.values() if scheme}
    if not insets:
        raise ToolError(
            "The model has no configured area scheme. Call setup_model first, or give insets."
        )
    by_number = {str(room["number"]): room for room in api("GET", "rooms", level=level)}
    areas: list[dict] = []
    errors: list[dict] = []
    created: dict[str, int] = {}
    for number in [str(number) for number in room_numbers]:
        if number not in by_number:
            errors.append(
                {"room_number": number, "error": f"No placed Room with number '{number}' on level '{level}'."}
            )
    for scheme, inset in insets.items():
        created[scheme] = 0
        for number in [str(number) for number in room_numbers]:
            room = by_number.get(number)
            if room is None:
                continue
            try:
                result = _draft_from_room(scheme, level, room, float(inset or 0), tolerance)
            except ToolError as error:
                errors.append({"room_number": number, "scheme": scheme, "error": str(error)})
                continue
            created[scheme] += 1
            areas.append(
                {
                    "scheme": scheme,
                    "number": result.get("number"),
                    "name": result.get("name"),
                    "id": result.get("id"),
                    "area": result.get("area"),
                    "inset": result["inset"],
                    "warnings": result["warnings"],
                    "program_point": result["program_point"],
                }
            )
    return {"level": level, "created": created, "areas": areas, "errors": errors}


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
def show_view(
    view: str,
    zoom_box: list[float] | None = None,
    fit: bool = False,
    view_type: str | None = None,
) -> dict:
    """Show a view in the Revit window. One call opens the view and zooms it.

    Args:
        view: View name or element id.
        zoom_box: Zoom a plan view to this rectangle: [x_min, y_min, x_max, y_max] in feet.
        fit: Zoom a plan view to its content: the Areas of an area plan, or the Rooms of the level if the view has no Areas. Ignored if zoom_box is given.
        view_type: Revit view type, for example FloorPlan or AreaPlan. Give it when two views have the same name (a floor plan and an area plan of the same level).
    """
    body = _clean({"view": view, "type": view_type})
    info = api("POST", "views/activate", body)
    if zoom_box is None and fit:
        zoom_box = _content_box(info)
        if zoom_box is None:
            info["zoomed"] = False
            info["zoom_note"] = "No Areas and no Rooms to zoom to."
            return info
    if zoom_box is None:
        return info
    # Revit opens a new view after the first request ends. The zoom works only
    # on an open view, so ask again until the view is open.
    body = {"view": str(info["id"]), "zoom_box": zoom_box}
    for attempt in range(6):
        info = api("POST", "views/activate", body)
        if info.get("zoomed"):
            break
        time.sleep(0.5)
    info["zoom_box"] = zoom_box
    return info


def _content_box(view_info: dict) -> list[float] | None:
    """Rectangle around the Areas (or the Rooms) of a plan view, with a margin."""
    level = view_info.get("level")
    if not level:
        return None
    points: list[list[float]] = []
    if view_info.get("area_scheme"):
        for area in api("GET", "areas", scheme=view_info["area_scheme"], level=level):
            points.extend(area.get("outline") or [])
    if not points:
        for room in api("GET", "rooms", level=level, outline="false"):
            if room.get("bbox"):
                points.append(room["bbox"][:2])
                points.append(room["bbox"][2:])
    if not points:
        return None
    x_min = min(point[0] for point in points)
    x_max = max(point[0] for point in points)
    y_min = min(point[1] for point in points)
    y_max = max(point[1] for point in points)
    margin = max(0.08 * max(x_max - x_min, y_max - y_min), 5.0)
    return [
        round(x_min - margin, 1),
        round(y_min - margin, 1),
        round(x_max + margin, 1),
        round(y_max + margin, 1),
    ]


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
    show: bool = True,
) -> list:
    """Make (or update) the shaded 3D view "PDSO - Program Volumes", color the Program Volumes by a program field, show the view in the Revit window, and return its image and legend. Program Volumes come from run_sync.

    Args:
        color_by: Program field that gives the color.
        pixel_width: Width of the image in pixels.
        building: "ghost" makes the other model elements transparent, so that the volumes in a building show. "show" keeps them as they are.
        section_box: Cut the view to a box around the Program Volumes.
        show: Make the 3D view the active view in the Revit window. Give false to keep the view that the user looks at.
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
    if show:
        api("POST", "views/activate", {"view": str(info["view"]["id"])})
    summary = {
        "view": info["view"],
        "legend": info["legend"],
        "file": str(path),
        "shown_in_revit": show,
    }
    return [json.dumps(summary), Image(data=data, format="png")]


# =============================================================================
# Open documents
# =============================================================================


@mcp.tool()
def list_open_documents() -> dict:
    """List the project documents that are open in Revit, which one is active, and which one these tools work on now."""
    return {"target_document": _target_document, "documents": api("GET", "documents")}


@mcp.tool()
def open_document_in_background(path: str, detach: bool = False) -> dict:
    """Open a Revit project file without a change of the active document or the Revit window. Use it when a person or a different agent works in the same Revit. Then call set_target_document with the title.

    Args:
        path: File path on the Revit computer.
        detach: Detach a workshared file from its central model.
    """
    return api(
        "POST", "documents/open", {"path": path, "detach": detach}, timeout=LONG_TIMEOUT
    )


@mcp.tool()
def set_target_document(document: str | None = None) -> dict:
    """Select the open Revit document that all the tools of this server work on. Without an argument, the tools work on the active document again. Tools that use the Revit window (selection, show_view) always use the active document.

    Args:
        document: Title of an open document (see list_open_documents), for example "snowdon_catalogue_sim".
    """
    global _target_document
    if document:
        titles = [item["title"] for item in api("GET", "documents")]
        if document not in titles and document.removesuffix(".rvt") not in titles:
            raise ToolError(
                f"Document '{document}' is not open. Open documents: {', '.join(titles)}"
            )
    _target_document = document or None
    return {"target_document": _target_document}


@mcp.tool()
def save_document(document: str | None = None) -> dict:
    """Save an open document to its file. It does not have to be the active document.

    Args:
        document: Title of the document. Default: the target document.
    """
    return api(
        "POST",
        "documents/save",
        {"document": document or _target_document},
        timeout=LONG_TIMEOUT,
    )


@mcp.tool()
def close_background_document(document: str, save: bool = False) -> dict:
    """Close an open document that is not the active document. Use close_project for the active document.

    Args:
        document: Title of the document.
        save: Save the document before it closes. Without it, changes that are not saved are lost.
    """
    global _target_document
    result = api("POST", "documents/close", {"document": document, "save": save}, timeout=LONG_TIMEOUT)
    if _target_document == document:
        _target_document = None
    return result


# =============================================================================
# Unit Layout Catalogue
# =============================================================================


@mcp.tool()
def list_layouts(program: str | None = None, project_type: str | None = None) -> dict:
    """List the layouts in the Unit Layout Catalogue: id, name, program, size, number of items, families, and the source room. A layout is the furniture, casework and equipment of one room, stored as a JSON file.

    Args:
        program: Only layouts for this program, for example "Studio Unit".
        project_type: Only layouts of this project type, for example "Residential".
    """
    return api("GET", "catalogue/layouts", program=program, project_type=project_type)


@mcp.tool()
def get_layout(layout_id: str) -> dict:
    """Get one layout completely: the stored JSON entry (room outline and each item with family, type, x, y, rotation, in the room's own frame) and its SVG plan sketch.

    Args:
        layout_id: Id of the layout (see list_layouts).
    """
    return api("GET", f"catalogue/layouts/{urllib.parse.quote(layout_id, safe='')}")


@mcp.tool()
def harvest_layout(
    room: str,
    name: str | None = None,
    program: str | None = None,
    project_type: str = "Other",
    notes: str = "",
) -> dict:
    """Save the layout of a Room or Area into the catalogue. The free-standing families in the room (furniture, casework, equipment, plumbing fixtures) are stored with their positions in the room's own frame, so the layout can go into a room of a different building. Wall-hosted and face-based families are skipped and listed. PDSO data silos and Program Volumes are not part of a layout. The family of each item is saved as a file in the families folder of the catalogue folder (family_files), so that place_layout can load it into a model that does not have it. Returns the JSON entry and its file path.

    Args:
        room: Room number (for example "304"), or the element id of a Room or Area.
        name: Name of the layout. Use a unique name; a layout with the same program and name replaces the earlier one. Default: "<program> - <area> sf".
        program: Program that the layout serves. Default: the Room name.
        project_type: For example "Residential", "Office" or "K-12".
        notes: Free text.
    """
    body = _clean(
        {
            "room": room,
            "name": name,
            "program": program,
            "project_type": project_type,
            "notes": notes,
        }
    )
    return api("POST", "catalogue/harvest", body, timeout=LONG_TIMEOUT)


@mcp.tool()
def recommend_layouts(room: str, program: str | None = None, limit: int = 10) -> dict:
    """Rank the catalogue layouts for one target Room or Area. A layout is in the list only if all its items are inside the target in one of 8 orientations. score is 60% area match and 40% size fit. shape_match (0 to 1) is how well the two outlines cover each other. Layouts for the same program come first.

    Args:
        room: Room number, or the element id of a Room or Area.
        program: Program of the target. Default: the Room name.
        limit: Largest number of layouts in the reply.
    """
    body = _clean({"room": room, "program": program, "limit": limit})
    return api("POST", "catalogue/recommend", body, timeout=LONG_TIMEOUT)


@mcp.tool()
def find_similar_rooms(
    layout_id: str,
    kind: str = "rooms",
    level: str | None = None,
    limit: int = 20,
) -> dict:
    """Find the Rooms (or Areas) of the model that are similar to a catalogue layout, ranked. Use it to find all rooms that can get the same layout. similarity is the average of area_match (the areas agree) and shape_match (the outlines cover each other); 1.0 is the same room shape and size. fits says if all items of the layout land in the room, and score is the recommender's score for that.

    Args:
        layout_id: Id of the layout (see list_layouts).
        kind: "rooms" or "areas".
        level: Only Rooms on this level.
        limit: Largest number of Rooms in the reply.
    """
    body = _clean(
        {"layout_id": layout_id, "kind": kind, "level": level, "limit": limit}
    )
    return api("POST", "catalogue/similar-rooms", body, timeout=LONG_TIMEOUT)


@mcp.tool()
def place_layout(room: str, layout_id: str) -> dict:
    """Place a catalogue layout into a target Room or Area. The recommender selects the orientation. A family that the model does not have is loaded first, from the family files that harvest_layout saved in the catalogue folder or from a different open model (see loaded_families). Returns the ids of the placed elements, the families that could not be loaded (these items are not placed), and the programs in the room that got the layout id in their unit_layout_type field. If the layout does not fit, nothing is placed.

    Args:
        room: Room number, or the element id of a Room or Area.
        layout_id: Id of the layout.
    """
    return api(
        "POST",
        "catalogue/place",
        {"room": room, "layout_id": layout_id},
        timeout=LONG_TIMEOUT,
    )


@mcp.tool()
def list_room_items(room: str) -> dict:
    """List the free-standing families in a Room or Area (the items that harvest_layout takes): id, category, family, type, x, y, z (project elevation), z_offset (height above the level), rotation.

    Args:
        room: Room number, or the element id of a Room or Area.
    """
    return api("GET", "catalogue/room-items", room=room)


@mcp.tool()
def describe_item(element_id: int) -> dict:
    """Describe one family instance (read-only): family, type, position, rotation, flips, its plan box in project coordinates, and the instance parameters that the family defines (for example the length of a counter top). Use it to compare a placed item with the original item.

    Args:
        element_id: Element id of the family instance (see list_room_items or the "placed" list of place_layout).
    """
    return api("GET", "catalogue/item", id=element_id)


@mcp.tool()
def clear_room_items(room: str) -> dict:
    """Delete the free-standing families in a Room or Area. Use it to make a furnished room empty before place_layout. This deletes elements from the model.

    Args:
        room: Room number, or the element id of a Room or Area.
    """
    return api("DELETE", "catalogue/room-items", room=room)


@mcp.tool()
def delete_layout(layout_id: str) -> dict:
    """Delete one layout file from the catalogue. A sample layout cannot be deleted.

    Args:
        layout_id: Id of the layout.
    """
    return api("DELETE", f"catalogue/layouts/{urllib.parse.quote(layout_id, safe='')}")


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


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    windows_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    regular = [
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        (str(windows_fonts / "arial.ttf"), 0),
        (str(windows_fonts / "segoeui.ttf"), 0),
    ]
    heavy = [
        ("/System/Library/Fonts/Helvetica.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
        (str(windows_fonts / "arialbd.ttf"), 0),
        (str(windows_fonts / "segoeuib.ttf"), 0),
    ]
    for name, index in (heavy + regular) if bold else regular:
        try:
            return ImageFont.truetype(name, size, index=index)
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


# =============================================================================
# Unit Layout Catalogue: previews of the suggested Rooms
# =============================================================================

# Colors of the preview images. The list window follows the suggestion list of
# a layout recommendation dialog: a thumbnail, the data of the Room, and the
# match in percent for each row, with the best row highlighted.
UI_WINDOW = (255, 255, 255)
UI_BORDER = (63, 169, 216)
UI_ROW = (233, 233, 233)
UI_ROW_SELECTED = (63, 169, 216)
UI_TEXT = (40, 40, 40)
UI_MUTED = (105, 105, 105)
UI_CHIP = (238, 238, 238)
UI_GOOD = (22, 130, 60)
UI_BAD = (196, 40, 40)
ROOM_FILL = (250, 250, 247, 255)
ROOM_LINE = (35, 35, 35, 255)
CURRENT_FILL = (214, 214, 214, 255)
CURRENT_LINE = (165, 165, 165, 255)
ITEM_FILL = (33, 111, 219, 95)
ITEM_LINE = (20, 84, 180, 255)
OUTSIDE_FILL = (220, 50, 40, 90)
OUTSIDE_LINE = (200, 30, 30, 255)
CROSS_FILL = (245, 150, 20, 115)
CROSS_LINE = (200, 105, 0, 255)
UI_WARN = (176, 96, 0)
# A footprint corner this far outside the Room outline counts as a crossing (ft)
CROSS_TOLERANCE = 0.5
LAYOUT_OUTLINE = (150, 60, 190, 255)


def _dashed_line(draw, start, end, fill, width, dash=9, gap=6) -> None:
    length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    if length < 1:
        return
    position = 0.0
    while position < length:
        a = position / length
        b = min(position + dash, length) / length
        draw.line(
            (
                start[0] + (end[0] - start[0]) * a,
                start[1] + (end[1] - start[1]) * a,
                start[0] + (end[0] - start[0]) * b,
                start[1] + (end[1] - start[1]) * b,
            ),
            fill=fill,
            width=width,
        )
        position += dash + gap


def _edge_distance(point, polygon) -> float:
    """Shortest distance from a point to the edges of a polygon."""
    best = float("inf")
    for i in range(len(polygon)):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % len(polygon)]
        dx, dy = bx - ax, by - ay
        squared = dx * dx + dy * dy
        t = 0.0
        if squared > 1e-12:
            t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / squared))
        best = min(best, math.hypot(point[0] - ax - t * dx, point[1] - ay - t * dy))
    return best


def _crossing_items(preview: dict) -> set[int]:
    """Indexes of the items whose footprint goes across the Room outline.

    place_layout only checks the insertion point of each item. An item can be
    in the Room by that check while its body is in a wall.
    """
    outline = preview["room"]["outline"]
    crossing = set()
    for index, item in enumerate(preview["items"]):
        if not item["inside"] or not item["footprint"]:
            continue
        for corner in item["footprint"]:
            if (
                not _inside(corner[0], corner[1], outline)
                and _edge_distance(corner, outline) > CROSS_TOLERANCE
            ):
                crossing.add(index)
                break
    return crossing


def _turned(point, turn: bool) -> tuple[float, float]:
    """Plan point for the drawing. turn: the plan is turned 90 degrees clockwise."""
    return (point[1], -point[0]) if turn else (point[0], point[1])


def _plan_extent(preview: dict, turn: bool, room: bool = True) -> tuple:
    points = list(preview["layout_outline"])
    if room:
        points += preview["room"]["outline"]
    points = [_turned(p, turn) for p in points]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _fit_text(draw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while len(text) > 1 and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _draw_plan(
    draw,
    preview: dict,
    box: tuple,
    scale: float,
    turn: bool,
    room: bool = True,
    numbers: bool = False,
) -> None:
    """Draw one Room with the items of the layout in it.

    Args:
        draw: ImageDraw with RGBA mode.
        preview: Reply of GET catalogue/preview.
        box: (left, top, right, bottom) in pixels. The plan is in its middle.
        scale: Pixels for each foot.
        turn: Turn the plan 90 degrees clockwise.
        room: Draw the Room and its current items. False: only the layout.
        numbers: Write the item numbers and the family names.
    """
    min_x, min_y, max_x, max_y = _plan_extent(preview, turn, room)
    left = (box[0] + box[2]) / 2 - (max_x - min_x) * scale / 2
    top = (box[1] + box[3]) / 2 - (max_y - min_y) * scale / 2

    def px(point) -> tuple[float, float]:
        x, y = _turned(point, turn)
        return (left + (x - min_x) * scale, top + (max_y - y) * scale)

    line = max(2, int(round(scale / 5)))
    thin = max(1, int(round(scale / 9)))
    if room:
        outline = [px(p) for p in preview["room"]["outline"]]
        draw.polygon(outline, fill=ROOM_FILL)
        for item in preview["current_items"]:
            if item["footprint"]:
                draw.polygon(
                    [px(p) for p in item["footprint"]],
                    fill=CURRENT_FILL,
                    outline=CURRENT_LINE,
                )
            else:
                x, y = px((item["x"], item["y"]))
                r = 0.4 * scale
                draw.ellipse((x - r, y - r, x + r, y + r), fill=CURRENT_FILL)
        draw.line(outline + [outline[0]], fill=ROOM_LINE, width=line, joint="curve")
    layout_outline = [px(p) for p in preview["layout_outline"]]
    if room:
        loop = layout_outline + [layout_outline[0]]
        for start, end in zip(loop, loop[1:]):
            _dashed_line(draw, start, end, LAYOUT_OUTLINE, thin + 1)
    else:
        draw.polygon(layout_outline, fill=ROOM_FILL)
        draw.line(
            layout_outline + [layout_outline[0]],
            fill=ROOM_LINE,
            width=line,
            joint="curve",
        )

    label_font = _font(max(11, min(17, int(scale * 0.62))))
    number_font = _font(max(11, min(18, int(scale * 0.7))), bold=True)
    crossing = _crossing_items(preview) if room else set()
    taken: list[tuple] = []
    for number, item in enumerate(preview["items"], start=1):
        x, y = px((item["x"], item["y"]))
        edge, fill = ITEM_LINE, ITEM_FILL
        if number - 1 in crossing:
            edge, fill = CROSS_LINE, CROSS_FILL
        if not item["inside"] or not item["loaded"]:
            edge, fill = OUTSIDE_LINE, OUTSIDE_FILL
        polygon = [px(p) for p in item["footprint"]] if item["footprint"] else None
        if polygon:
            draw.polygon(polygon, fill=fill)
            draw.line(polygon + [polygon[0]], fill=edge, width=thin + 1)
        else:
            r = 0.55 * scale
            draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=edge, width=thin + 1)
        # Direction tick: from the insertion point, along the rotation of the item
        dx, dy = _turned(
            (math.cos(item["rotation"]), math.sin(item["rotation"])), turn
        )
        tick = max(1.3 * scale, 7)
        draw.line((x, y, x + dx * tick, y - dy * tick), fill=edge, width=thin + 1)
        r = max(2.5, 0.2 * scale)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=edge)
        if not numbers:
            continue
        if polygon:
            cx = sum(p[0] for p in polygon) / 4
            cy = sum(p[1] for p in polygon) / 4
            wide = max(p[0] for p in polygon) - min(p[0] for p in polygon)
            high = max(p[1] for p in polygon) - min(p[1] for p in polygon)
        else:
            cx, cy, wide, high = x, y - scale, 0, 0
        text = str(number)
        text_width = draw.textlength(text, font=number_font)
        name = item["family"]
        name_width = draw.textlength(name, font=label_font)
        if name_width + 10 <= wide and high >= number_font.size + label_font.size + 8:
            top_text = cy - (number_font.size + label_font.size + 2) / 2
            draw.text((cx - text_width / 2, top_text), text, fill=(10, 40, 110, 255), font=number_font)
            draw.text(
                (cx - name_width / 2, top_text + number_font.size + 2),
                name,
                fill=(10, 40, 110, 255),
                font=label_font,
            )
        else:
            pad = 3
            half_w = text_width / 2 + pad
            half_h = number_font.size / 2 + pad
            # Move the number if it is on top of an earlier number
            for shift_x, shift_y in ((0, 0), (0, 2.2), (0, -2.2), (2.2, 0), (-2.2, 0), (2.2, 2.2), (-2.2, -2.2)):
                tx, ty = cx + shift_x * half_w, cy + shift_y * half_h
                if not any(abs(tx - ox) < 2 * half_w and abs(ty - oy) < 2 * half_h for ox, oy in taken):
                    cx, cy = tx, ty
                    break
            taken.append((cx, cy))
            draw.rectangle(
                (cx - half_w, cy - half_h, cx + half_w, cy + half_h),
                fill=(255, 255, 255, 225),
            )
            draw.text(
                (cx - text_width / 2, cy - number_font.size / 2 - 1),
                text,
                fill=(10, 40, 110, 255),
                font=number_font,
            )


def _fit_text_line(preview: dict) -> tuple[str, str]:
    """One line that says if the layout fits the Room, and "good", "warn" or "bad"."""
    match = preview["match"]
    count = len(preview["items"])
    if match["fits"]:
        text = f"Fits: all {count} insertion points are in the Room ({match['placement']['description']})"
        state = "good"
        crossing = len(_crossing_items(preview))
        if crossing:
            text += f", but {crossing} item{' goes' if crossing == 1 else 's go'} across the Room outline"
            state = "warn"
        if match.get("items_to_load"):
            text += f"; {len(match['families_to_load'])} families will be loaded"
        if match["items_not_loaded"]:
            text += f"; {match['items_not_loaded']} items: family not loaded"
            state = "warn"
        return text, state
    if match["items_outside"]:
        return (
            f"Does not fit: {match['items_outside']} of {count} items are outside the Room",
            "bad",
        )
    layout = preview["layout"]
    room = preview["room"]
    return (
        f"Does not fit: the layout ({layout['width']:.1f} x {layout['depth']:.1f} ft) is larger"
        f" than the Room ({room['width']:.1f} x {room['depth']:.1f} ft)",
        "bad",
    )


UI_STATE = {"good": UI_GOOD, "warn": UI_WARN, "bad": UI_BAD}


def _percent(value: float) -> str:
    text = f"{value * 100:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "%"


def _window(width: int, height: int, title: str):
    image = PILImage.new("RGB", (width, height), UI_WINDOW)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((0, 0, width - 1, height - 1), outline=UI_BORDER, width=3)
    draw.rectangle((22, 20, 34, 32), fill=UI_BORDER)
    draw.rectangle((38, 20, 50, 32), fill=(120, 196, 228))
    draw.rectangle((22, 36, 34, 48), fill=(120, 196, 228))
    draw.rectangle((38, 36, 50, 48), fill=UI_BORDER)
    draw.text((62, 19), title, fill=UI_TEXT, font=_font(26))
    return image, draw


def _legend(draw, x: float, y: float, font, room: bool = True) -> float:
    """Draw the legend of the plan symbols in one row. Returns the right end."""
    entries = [("box", ITEM_FILL, ITEM_LINE, "item of the layout (tick: direction)")]
    if room:
        entries += [
            ("box", CURRENT_FILL, CURRENT_LINE, "furniture in the Room now"),
            ("dash", None, LAYOUT_OUTLINE, "source room outline"),
            ("box", CROSS_FILL, CROSS_LINE, "item across the Room outline"),
            ("box", OUTSIDE_FILL, OUTSIDE_LINE, "item outside, or family not loaded"),
        ]
    for kind, fill, edge, text in entries:
        if kind == "box":
            draw.rectangle((x, y + 2, x + 26, y + 18), fill=fill, outline=edge, width=2)
        else:
            _dashed_line(draw, (x, y + 10), (x + 26, y + 10), edge, 3, 7, 4)
        draw.text((x + 34, y), text, fill=UI_MUTED, font=font)
        x += 34 + draw.textlength(text, font=font) + 30
    return x


def _draw_room_list(layout: dict, previews: list[dict], rooms_checked: int, width: int):
    """The list window: one row for each suggested Room, ranked."""
    extents = [_plan_extent(p, False) for p in previews]
    span_x = max(e[2] - e[0] for e in extents)
    span_y = max(e[3] - e[1] for e in extents)
    turn = span_y > span_x  # show the long side of the Rooms horizontal
    if turn:
        span_x, span_y = span_y, span_x
    pad = 12
    scale = min((width * 0.25 - 2 * pad) / span_x, 205.0 / span_y)
    thumb_width = int(span_x * scale + 2 * pad + 36)
    row_height = int(max(span_y * scale + 2 * pad, 150))
    gap = 4
    header = 262
    footer = 58
    height = header + len(previews) * (row_height + gap) + footer
    image, draw = _window(width, height, "PDSO Unit Layout Catalogue")

    label = _font(21)
    value = _font(21)
    source = layout.get("source") or {}
    chips = [
        ("Layout", layout["name"], 0.27),
        ("Program", layout["program"], 0.12),
        ("Project type", layout["project_type"], 0.11),
        ("Size", f"{layout['area']:,.0f} SF, {layout['width']:.1f} x {layout['depth']:.1f} ft", 0.17),
        ("Items", str(layout["item_count"]), 0.06),
    ]
    stored_width = int(span_x * scale * 0.62) + 2 * pad
    chip_area = width - 60 - stored_width - 30
    x = 30
    for name, text, share in chips:
        chip_width = chip_area * share / sum(c[2] for c in chips)
        draw.text((x, 72), name, fill=UI_MUTED, font=label)
        draw.rectangle((x, 102, x + chip_width - 12, 140), fill=UI_CHIP)
        draw.rectangle((x + 10, 112, x + 28, 130), fill="white", outline=UI_TEXT)
        draw.line((x + 13, 121, x + 18, 126, x + 25, 115), fill=UI_TEXT, width=2)
        draw.text(
            (x + 38, 108),
            _fit_text(draw, text, value, chip_width - 62),
            fill=UI_TEXT,
            font=value,
        )
        x += chip_width
    project = source.get("project") or "?"
    draw.text(
        (30, 158),
        f"Source: Room '{source.get('room_name', '')}' on {source.get('level', '?')} of {project}"
        f", harvested {source.get('harvested', '?')}",
        fill=UI_MUTED,
        font=label,
    )
    draw.text(
        (30, 212),
        f"Suggested Rooms  (best {len(previews)} of {rooms_checked} Rooms; no change to the model."
        f" All plans: same scale{', turned 90 deg clockwise' if turn else ''})",
        fill=UI_TEXT,
        font=_font(23),
    )
    # The stored layout, at 62 % of the scale of the rows
    stored_left = width - 30 - stored_width
    draw.text((stored_left, 62), "Stored layout", fill=UI_MUTED, font=label)
    draw.rectangle(
        (stored_left, 90, stored_left + stored_width, header - 14),
        fill="white",
        outline=(200, 200, 200),
    )
    _draw_plan(
        draw,
        previews[0],
        (stored_left, 90, stored_left + stored_width, header - 14),
        min(scale * 0.62, (header - 104 - 2 * pad) / span_y),
        turn,
        room=False,
    )

    title_font = _font(27)
    text_font = _font(22)
    match_font = _font(27)
    for index, preview in enumerate(previews):
        top = header + index * (row_height + gap)
        selected = index == 0
        room = preview["room"]
        match = preview["match"]
        draw.rectangle(
            (30, top, width - 30, top + row_height),
            fill=UI_ROW_SELECTED if selected else UI_ROW,
        )
        thumb = (32, top + 2, 30 + thumb_width, top + row_height - 2)
        draw.rectangle(thumb, fill="white")
        _draw_plan(draw, preview, thumb, scale, turn)
        ink = (255, 255, 255) if selected else UI_TEXT
        soft = (255, 255, 255) if selected else UI_MUTED
        x = 30 + thumb_width + 26
        y = top + max(14, (row_height - 150) // 2)
        draw.text(
            (x, y),
            f"{index + 1}.  Room {room['number']}: {room['name']}",
            fill=ink,
            font=title_font,
        )
        draw.text((x, y + 38), f"Level {room['level']}", fill=soft, font=text_font)
        fit, state = _fit_text_line(preview)
        draw.text(
            (x, y + 68),
            fit,
            fill=ink if selected else UI_STATE[state],
            font=text_font,
        )
        draw.text(
            (x, y + 106),
            f"Area: {room['area']:,.0f} SF   ({room['width']:.1f} x {room['depth']:.1f} ft)",
            fill=ink,
            font=text_font,
        )
        right = width - 30 - 26
        lines = [
            (f"Area match: {_percent(match['area_match'])}", text_font, soft),
            (f"Shape match: {_percent(match['shape_match'])}", text_font, soft),
            (f"similarity {match['similarity']:.3f}", text_font, soft),
            (f"Match: {_percent(match['similarity'])}", match_font, ink),
        ]
        line_y = y
        for text, font, color in lines:
            if font is match_font:
                line_y += 8
            draw.text(
                (right - draw.textlength(text, font=font), line_y),
                text,
                fill=color,
                font=font,
            )
            line_y += font.size + 8
    footer_y = height - footer + 16
    _legend(draw, 30, footer_y, _font(19))
    return image


def _draw_room_panel(layout: dict, preview: dict, rank: int, count: int, width: int):
    """The large picture of one suggested Room, north up, with the item list."""
    height = int(width * 0.62)
    room = preview["room"]
    match = preview["match"]
    image, draw = _window(
        width,
        height,
        f"PDSO Unit Layout Catalogue  -  suggestion {rank} of {count}",
    )
    side = int(width * 0.36)
    plan_box = (30, 70, width - side - 20, height - 62)
    draw.rectangle(plan_box, fill=(246, 246, 246))
    full_box = plan_box
    plan_box = (plan_box[0], plan_box[1], plan_box[2], plan_box[3] - 40)  # scale bar below
    min_x, min_y, max_x, max_y = _plan_extent(preview, False)
    scale = min(
        (plan_box[2] - plan_box[0] - 60) / (max_x - min_x),
        (plan_box[3] - plan_box[1] - 50) / (max_y - min_y),
    )
    _draw_plan(draw, preview, plan_box, scale, False, numbers=True)
    small = _font(19)
    bar_feet = next((b for b in (20, 10, 5, 2, 1) if b * scale <= 220), 1)
    plan_box = full_box
    bar_y = plan_box[3] - 22
    draw.line((plan_box[0] + 20, bar_y, plan_box[0] + 20 + bar_feet * scale, bar_y), fill="black", width=4)
    draw.text((plan_box[0] + 30 + bar_feet * scale, bar_y - 12), f"{bar_feet} ft", fill="black", font=small)
    arrow_x, arrow_y = plan_box[2] - 34, plan_box[1] + 62
    draw.line((arrow_x, arrow_y, arrow_x, arrow_y - 36), fill="black", width=3)
    draw.polygon(
        [(arrow_x, arrow_y - 46), (arrow_x - 8, arrow_y - 30), (arrow_x + 8, arrow_y - 30)],
        fill="black",
    )
    draw.text((arrow_x - 7, arrow_y + 2), "N", fill="black", font=small)

    x = width - side + 6
    wide = side - 40
    draw.rectangle((x - 10, 70, width - 30, 70 + 196), fill=UI_ROW_SELECTED)
    draw.text((x + 6, 82), f"Room {room['number']}: {room['name']}", fill="white", font=_font(31))
    draw.text(
        (x + 6, 124),
        f"Level {room['level']}   |   {room['area']:,.0f} SF   |   {room['width']:.1f} x {room['depth']:.1f} ft",
        fill="white",
        font=_font(22),
    )
    draw.text((x + 6, 160), f"Match: {_percent(match['similarity'])}", fill="white", font=_font(40))
    text = f"similarity {match['similarity']:.3f}"
    draw.text(
        (width - 46 - draw.textlength(text, font=_font(22)), 176),
        text,
        fill="white",
        font=_font(22),
    )
    draw.text(
        (x + 6, 216),
        f"Area match {_percent(match['area_match'])}   |   Shape match {_percent(match['shape_match'])}",
        fill="white",
        font=_font(22),
    )
    y = 282
    fit, state = _fit_text_line(preview)
    for part in _wrap(draw, fit, _font(23), wide):
        draw.text((x, y), part, fill=UI_STATE[state], font=_font(23))
        y += 30
    y += 4
    for part in _wrap(
        draw,
        f"Layout: {layout['name']} ({layout['area']:,.0f} SF). Orientation from: {match['orientation_from']}."
        f" Furniture in the Room now: {len(preview['current_items'])} items.",
        small,
        wide,
    ):
        draw.text((x, y), part, fill=UI_MUTED, font=small)
        y += 25
    y += 12
    draw.text((x, y), "Items of the layout", fill=UI_TEXT, font=_font(22, bold=True))
    y += 32
    crossing = _crossing_items(preview)
    rows = len(preview["items"])
    step = min(27, max(17, (height - 70 - y) // max(rows, 1)))
    item_font = _font(min(20, step - 5))
    for number, item in enumerate(preview["items"], start=1):
        note = "" if item["loaded"] else "  (family not loaded)"
        note += "" if item["inside"] else "  (outside)"
        color = UI_TEXT if item["inside"] and item["loaded"] else UI_BAD
        if number - 1 in crossing:
            note, color = "  (across the outline)", UI_WARN
        draw.text((x, y), f"{number}", fill=color, font=item_font)
        draw.text(
            (x + 34, y),
            _fit_text(draw, f"{item['family']} : {item['type']}{note}", item_font, wide - 34),
            fill=color,
            font=item_font,
        )
        y += step
    _legend(draw, 30, height - 44, _font(19))
    return image


def _wrap(draw, text: str, font, width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + ([current] if current else [])


@mcp.tool()
def preview_similar_rooms(
    layout_id: str,
    limit: int = 5,
    kind: str = "rooms",
    level: str | None = None,
    include_source_room: bool = False,
    pixel_width: int = 1920,
) -> list:
    """Show pictures of the Rooms that find_similar_rooms suggests for a layout, before anything is placed. This tool does not change the model. The first image is a ranked list: one row for each Room with a plan thumbnail, the Room number, name, level and area, the area match, the shape match, the match in percent (the similarity), and if the layout fits. Then there is one large image for each Room (north up) with the item numbers and the family names. Each plan shows the Room outline, the items of the layout at the positions and rotations that place_layout would use (blue, with a direction tick), the furniture that is in the Room now (grey), and the outline of the source room (dashed). All thumbnails have the same scale.

    Args:
        layout_id: Id of the layout (see list_layouts).
        limit: Number of Rooms (1 to 12). Default: the best 5.
        kind: "rooms" or "areas".
        level: Only Rooms on this level.
        include_source_room: Keep the Room that the layout came from in the list.
        pixel_width: Width of each image in pixels (1200 to 4000).
    """
    limit = max(1, min(int(limit), 12))
    pixel_width = max(1200, min(int(pixel_width), 4000))
    body = _clean({"layout_id": layout_id, "kind": kind, "level": level, "limit": limit + 1})
    similar = api("POST", "catalogue/similar-rooms", body, timeout=LONG_TIMEOUT)
    rooms = [
        room
        for room in similar["rooms"]
        if include_source_room or not room.get("is_source")
    ][:limit]
    if not rooms:
        raise ToolError("The model has no Room to compare with this layout.")
    previews = [
        api("GET", "catalogue/preview", room=room["id"], layout_id=layout_id, timeout=LONG_TIMEOUT)
        for room in rooms
    ]
    layout = similar["layout"]
    stem = f"preview_{layout_id[:40]}"
    images = []
    sheet = _draw_room_list(layout, previews, similar["rooms_checked"], pixel_width)
    sheet_path = _image_path(f"{stem}_list")
    sheet.save(sheet_path)
    images.append(Image(data=sheet_path.read_bytes(), format="png"))
    summary = {"layout_id": layout_id, "rooms_checked": similar["rooms_checked"], "list_image": str(sheet_path), "rooms": []}
    for rank, preview in enumerate(previews, start=1):
        panel = _draw_room_panel(layout, preview, rank, len(previews), pixel_width)
        path = _image_path(f"{stem}_{rank}_room_{preview['room']['number']}")
        panel.save(path)
        images.append(Image(data=path.read_bytes(), format="png"))
        match = preview["match"]
        summary["rooms"].append(
            {
                "rank": rank,
                "id": preview["room"]["id"],
                "number": preview["room"]["number"],
                "name": preview["room"]["name"],
                "level": preview["room"]["level"],
                "area": preview["room"]["area"],
                "similarity": match["similarity"],
                "area_match": match["area_match"],
                "shape_match": match["shape_match"],
                "fits": match["fits"],
                "fit_text": _fit_text_line(preview)[0],
                "reason": match["reason"],
                "items_across_outline": len(_crossing_items(preview)),
                "placement": match["placement"]["description"],
                "items": len(preview["items"]),
                "items_outside": match["items_outside"],
                "items_not_loaded": match["items_not_loaded"],
                "current_items": len(preview["current_items"]),
                "image": str(path),
            }
        )
    return [json.dumps(summary), *images]


if __name__ == "__main__":
    mcp.run()
