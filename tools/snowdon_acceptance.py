#!/usr/bin/env python3
"""Acceptance and regression runner for the PDSO HTTP API.

The runner drives the API in a real Revit (see startup.py at the repository root). Each
step prints one line: ``STEP <id> PASS|FAIL <evidence>``. The exit code is 1 if
a step fails.

Usage:
    tools/snowdon_acceptance.py --model empty --steps R0 --save-baseline
    tools/snowdon_acceptance.py --model empty --steps R0,G1,P1,P2,P3,P4,P5,S1,S2,S3
    tools/snowdon_acceptance.py --model empty2 --steps B1,B2,V1,X1,V2,O1,C1,D1
    tools/snowdon_acceptance.py --model snowdon --steps A1-A10
    tools/snowdon_acceptance.py --model sample --steps A1-A6

Models (files on the Revit computer, in PDSO_MODEL_DIR):
    empty    acc_empty.rvt    a new project, made by step R0
    empty2   acc_empty2.rvt   a new project, made by step B1
    snowdon  acc_snowdon.rvt  a fresh copy of "Demo Snowdon Architectural.rvt"
    sample   acc_sample.rvt   a fresh copy of the Autodesk sample project

Environment:
    PDSO_API_URL    Default: http://127.0.0.1:48884/pdso
    PDSO_SSH_HOST   SSH target of the Revit computer (user@host). If set, the
                    runner opens an SSH port forward to it when the API does
                    not answer. Not set: Revit is on this computer.
    PDSO_MODEL_DIR  Folder of the model files on the Revit computer (necessary)
    PDSO_OUTPUT_DIR Folder for images. Default: <temp dir>/pdso-acceptance

The steps of one model depend on the steps before them. Run them in order.
D1 also opens acc_empty.rvt in the background: run R0 (model empty) once first.
C1 needs a furniture, casework or specialty equipment family in the project
template.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_URL = os.environ.get("PDSO_API_URL", "http://127.0.0.1:48884/pdso").rstrip("/")
SSH_HOST = os.environ.get("PDSO_SSH_HOST", "")
MODEL_DIR = os.environ.get("PDSO_MODEL_DIR", "")
OUTPUT_DIR = Path(
    os.environ.get("PDSO_OUTPUT_DIR", Path(tempfile.gettempdir()) / "pdso-acceptance")
)
BASELINE = Path(__file__).with_name("baseline_empty.json")

MODEL_FILES = {
    "empty": "acc_empty.rvt",
    "empty2": "acc_empty2.rvt",
    "snowdon": "acc_snowdon.rvt",
    "sample": "acc_sample.rvt",
}

TENANT = "Tenant Type"
RENT = "Rent per SF"
MAPPINGS_OWN_SCHEMES = {"PDSO - GSF": "Gross Building", "PDSO - NSF": "Rentable"}

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_tunnel: subprocess.Popen | None = None


class ApiFailure(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class StepFailure(Exception):
    pass


# =============================================================================
# HTTP
# =============================================================================


def _open_tunnel() -> None:
    global _tunnel
    port = urllib.parse.urlparse(API_URL).port or 80
    _tunnel = subprocess.Popen(
        ["ssh", "-N", "-L", f"{port}:127.0.0.1:48884", SSH_HOST],
        stdin=subprocess.DEVNULL,
    )
    atexit.register(_tunnel.terminate)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.25)
    raise SystemExit("The SSH port forward did not open.")


def _request(method: str, path: str, body: Any, query: dict, timeout: int) -> Any:
    query = {key: value for key, value in query.items() if value is not None}
    url = f"{API_URL}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    status = 200
    try:
        raw = _OPENER.open(request, timeout=timeout).read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ApiFailure(status, f"reply is not JSON: {raw[:300]!r}")
    if isinstance(payload, dict) and "exception" in payload:
        raise ApiFailure(500, f"pyRevit Routes: {payload['exception']}")
    if not payload.get("ok"):
        error = payload.get("error", {})
        message = error.get("message", "unknown error")
        if error.get("type") != "ApiError" and error.get("traceback"):
            message += "\n" + error["traceback"][-1500:]
        raise ApiFailure(status, message)
    return payload["data"]


def api(
    method: str, path: str, body: Any = None, timeout: int = 1800, **query: Any
) -> Any:
    """Call the API. Open the SSH port forward at the first connection error.

    The request is sent again only if it did not reach Revit (urllib wraps a
    connect error, such as a refused connection, in URLError) or if it is a
    GET. A dropped connection after a write stops the run.
    """
    try:
        return _request(method, path, body, query, timeout)
    except (urllib.error.URLError, ConnectionError) as error:
        if not SSH_HOST or _tunnel is not None:
            raise SystemExit(f"The PDSO API does not answer at {API_URL}: {error}")
        reason = getattr(error, "reason", error)
        dropped = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
        # A timeout while sending is wrapped too, but the request may have
        # reached Revit
        not_sent = isinstance(error, urllib.error.URLError) and not isinstance(
            reason, dropped + (TimeoutError,)
        )
        if method != "GET" and not not_sent:
            raise SystemExit(
                f"The connection dropped during {method} {path} ({error}); Revit "
                "may have made the change. Check the model before you run again."
            )
        _open_tunnel()
        return _request(method, path, body, query, timeout)


def status_of(method: str, path: str, body: Any = None, **query: Any) -> int:
    """HTTP status of a call that is expected to fail (200 if it does not)."""
    try:
        api(method, path, body, **query)
    except ApiFailure as failure:
        return failure.status
    return 200


def failure_of(method: str, path: str, body: Any = None, **query: Any) -> ApiFailure:
    try:
        api(method, path, body, **query)
    except ApiFailure as failure:
        return failure
    raise StepFailure(f"{method} {path} did not fail")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise StepFailure(message)


def revit_exec(code: str) -> Any:
    """Run Python in Revit (POST dev/exec). The code sets ``result``."""
    reply = api("POST", "dev/exec", {"code": code})
    if reply.get("error"):
        raise StepFailure("dev/exec: " + reply["error"][-800:])
    return reply["result"]


# =============================================================================
# Step registry
# =============================================================================

STEPS: dict[str, Callable[[], str]] = {}
STATE: dict[str, Any] = {}


def step(step_id: str):
    def wrapper(func: Callable[[], str]):
        STEPS[step_id] = func
        return func

    return wrapper


def model_path(model: str) -> str:
    if not MODEL_DIR:
        raise SystemExit(
            "Set PDSO_MODEL_DIR to the model folder on the Revit computer."
        )
    return MODEL_DIR.rstrip("\\/") + "\\" + MODEL_FILES[model]


def new_project(model: str) -> None:
    api("POST", "document/new", {"path": model_path(model), "overwrite": True})
    names = {level["name"] for level in api("GET", "levels")}
    for name, elevation in (("L1", 0.0), ("L2", 12.0)):
        if name not in names:
            api("POST", "levels", {"name": name, "elevation": elevation})


def rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def program_evidence(program: dict) -> dict:
    """A program without the values that change from run to run."""
    skip = {"id", "last_synced", "program_volumes", "areas", "location"}
    data = {key: value for key, value in program.items() if key not in skip}
    data["area_schemes"] = sorted(
        name for name, area in program.get("areas", {}).items() if area
    )
    data["volume_levels"] = sorted(
        instance["level"]
        for instance in program.get("program_volumes", {}).get("instances", [])
    )
    return data


def run_sync() -> dict:
    result = api("POST", "sync", {})
    check(result.get("ran") is True, f"sync did not run: {result.get('reason')}")
    check(
        result.get("success") is True,
        f"sync failed: {result.get('errors')} {result.get('message')}",
    )
    return result


def set_custom_values() -> None:
    api(
        "PUT",
        "programs/AA-01-AA",
        {"fields": {"tenant_type": "Retail", "rent_per_sf": 42.5}},
    )


def tracked() -> list[dict]:
    """The tracked parameters of the model."""
    return [item for item in api("GET", "parameters")["parameters"] if item["tracked"]]


def ensure_custom_parameters() -> None:
    fields = {item["field"] for item in tracked()}
    if "tenant_type" not in fields:
        api("POST", "parameters", {"name": TENANT, "type": "string"})
    if "rent_per_sf" not in fields:
        api("POST", "parameters", {"name": RENT, "type": "number"})


# =============================================================================
# Model "empty": regression and feature steps
# =============================================================================


@step("R0")
def r0() -> str:
    """Regression: the standard flow on a new project gives the baseline."""
    new_project("empty")
    api("POST", "bootstrap", {})
    for scheme in ("PDSO - GSF", "PDSO - NSF", "PDSO - ZFA"):
        api(
            "POST",
            "areas",
            {"scheme": scheme, "level": "L1", "points": rect(0, 0, 40, 30)},
        )
    api(
        "POST",
        "programs",
        {
            "level": "L1",
            "x": 20,
            "y": 15,
            "fields": {"uid": "AA-01-AA", "program_type": "Residential"},
        },
    )
    sync = run_sync()
    schema = [
        {key: value for key, value in item.items() if key != "standard"}
        for item in api("GET", "programs/schema")
        if item.get("standard", True)
    ]
    evidence = {
        "schema": schema,
        "program": program_evidence(api("GET", "programs/AA-01-AA")),
        "sync": {key: sync.get(key) for key in ("completed", "success", "phases")},
    }
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if STATE.get("save_baseline"):
        BASELINE.write_text(text + "\n")
        return f"baseline saved: {len(schema)} fields, {BASELINE}"
    check(BASELINE.exists(), f"no baseline file {BASELINE}; use --save-baseline")
    expected = json.loads(BASELINE.read_text())
    if expected != evidence:
        diff = [
            key
            for key in ("schema", "program", "sync")
            if expected.get(key) != evidence.get(key)
        ]
        raise StepFailure(
            f"differs from the baseline in {diff}: "
            f"{json.dumps({key: evidence[key] for key in diff}, sort_keys=True)[:1200]}"
        )
    program = evidence["program"]
    return (
        f"equal to baseline: {len(schema)} fields, gsf={program['gsf']}, "
        f"nsf={program['nsf']}, zfa={program['zfa']}, "
        f"volumes on {program['volume_levels']}"
    )


@step("G1")
def g1() -> str:
    schema = api("GET", "programs/schema")
    standard = [item for item in schema if item.get("standard") is True]
    check(len(standard) == 20, f"{len(standard)} standard fields, expected 20")
    found = api("GET", "programs", program_type="Residential")
    check(
        [program["uid"] for program in found] == ["AA-01-AA"],
        f"filter gave {[program['uid'] for program in found]}",
    )
    status = status_of("GET", "programs", nope="1")
    check(status == 400, f"unknown filter gave status {status}, expected 400")
    return (
        "20 standard fields; filter program_type=Residential -> AA-01-AA; nope -> 400"
    )


@step("P1")
def p1() -> str:
    before = len(tracked())
    check(before == 20, f"{before} tracked parameters after the bootstrap, expected 20")
    first = api("POST", "parameters", {"name": TENANT, "type": "string"})
    second = api("POST", "parameters", {"name": RENT, "type": "number"})
    for reply, name, field, kind in (
        (first, TENANT, "tenant_type", "string"),
        (second, RENT, "rent_per_sf", "number"),
    ):
        check(reply["name"] == name, f"name {reply['name']!r}")
        check(reply["field"] == field, f"field {reply['field']!r}")
        check(reply["type"] == kind, f"type {reply['type']!r}")
        check(reply["role"] == "custom", f"role {reply['role']!r}")
        check(reply["standard"] is False, "standard is not false")
        check(reply["bound"] is True, f"{name} is not bound: {reply['problem']}")
        check(reply["reattached"] is False, "reattached is not false")
    count = len(tracked())
    check(count == 22, f"{count} tracked parameters, expected 22")
    return f"created {first['name']} ({first['guid']}), {second['name']}; 22 tracked"


@step("P2")
def p2() -> str:
    results = {}
    for name in ("Program Type", "program-type", "PDSO - Program Type", TENANT):
        results[name] = status_of(
            "POST", "parameters", {"name": name, "type": "string"}
        )
        check(results[name] == 409, f"{name!r} gave {results[name]}, expected 409")
    blank = status_of("POST", "parameters", {"name": "  ", "type": "string"})
    check(blank == 400, f"blank name gave {blank}, expected 400")
    bad_type = status_of("POST", "parameters", {"name": "Other", "type": "ElementId"})
    check(bad_type == 400, f"type ElementId gave {bad_type}, expected 400")
    count = len(tracked())
    check(count == 22, f"{count} tracked parameters after rejected calls, expected 22")
    return f"409 for {sorted(results)}; 400 for blank name and type ElementId; still 22"


@step("P3")
def p3() -> str:
    ensure_custom_parameters()
    set_custom_values()
    program = api("GET", "programs/AA-01-AA")
    check(program["tenant_type"] == "Retail", f"tenant_type {program['tenant_type']!r}")
    check(program["rent_per_sf"] == 42.5, f"rent_per_sf {program['rent_per_sf']!r}")
    found = api("GET", "programs", tenant_type="Retail")
    check(len(found) == 1, f"filter tenant_type=Retail gave {len(found)} programs")
    groups = api("GET", "programs/summary", group_by="tenant_type")
    check(
        any(group["value"] == "Retail" for group in groups),
        f"summary groups {[group['value'] for group in groups]}",
    )
    return "tenant_type=Retail, rent_per_sf=42.5 read back; filter and summary use it"


@step("P4")
def p4() -> str:
    failure = failure_of("PUT", "programs/AA-01-AA", {"fields": {"rent_per_sf": "abc"}})
    check(failure.status == 400, f"'abc' gave {failure.status}, expected 400")
    check("rent_per_sf" in failure.message, f"message {failure.message!r}")
    api("PUT", "programs/AA-01-AA", {"fields": {"rent_per_sf": "12.5"}})
    value = api("GET", "programs/AA-01-AA")["rent_per_sf"]
    check(value == 12.5, f"rent_per_sf {value!r}, expected 12.5")
    return f"'abc' -> 400 ({failure.message!r}); '12.5' -> 12.5"


@step("P5")
def p5() -> str:
    api("PUT", "programs/AA-01-AA", {"fields": {"tenant_type": "Retail"}})
    reply = api("DELETE", "parameters/tenant_type")
    check(reply["purged"] is False and reply["detached"] is True, f"delete: {reply}")
    check(len(tracked()) == 21, "expected 21 tracked parameters after delete")
    check(
        "tenant_type" not in api("GET", "programs/AA-01-AA"),
        "the program still has the field tenant_type",
    )
    again = api("POST", "parameters", {"name": TENANT, "type": "string"})
    check(again["reattached"] is True, f"reattached is {again['reattached']}")
    value = api("GET", "programs/AA-01-AA")["tenant_type"]
    check(value == "Retail", f"value after reattach is {value!r}, expected 'Retail'")

    api("DELETE", "parameters/tenant_type")
    mismatch = status_of("POST", "parameters", {"name": TENANT, "type": "number"})
    check(mismatch == 409, f"reattach with other type gave {mismatch}, expected 409")
    untracked = status_of("DELETE", "parameters/tenant_type", purge="true")
    check(untracked == 404, f"purge of an untracked parameter gave {untracked}")
    api("POST", "parameters", {"name": TENANT, "type": "string"})
    purged = api("DELETE", "parameters/tenant_type", purge="true")
    check(purged["purged"] is True, f"purge: {purged}")
    api("DELETE", "parameters/rent_per_sf", purge="true")
    check(len(tracked()) == 20, "expected 20 tracked parameters after purge")
    saved = api("GET", "config")["tethered_parameters"]
    check(len(saved) == 20, f"the config has {len(saved)} tracked parameters")

    api("POST", "parameters", {"name": TENANT, "type": "string"})
    value = api("GET", "programs/AA-01-AA")["tenant_type"]
    check(value in ("", None), f"value after purge is {value!r}, expected empty")
    api("DELETE", "parameters/tenant_type", purge="true")

    locked = status_of("DELETE", "parameters/uid")
    check(locked == 409, f"delete of the required parameter uid gave {locked}")
    mapped = status_of("DELETE", "parameters/gsf")
    check(mapped == 409, f"delete of the area-mapped parameter gsf gave {mapped}")
    unknown = status_of("DELETE", "parameters/nope")
    check(unknown == 404, f"delete of an unknown parameter gave {unknown}")
    return (
        "delete keeps the value; create again gives 'Retail' back; other type -> 409; "
        "purge deletes the value; uid and gsf -> 409; unknown -> 404; 20 tracked"
    )


@step("S1")
def s1() -> str:
    ensure_custom_parameters()
    set_custom_values()
    api("PUT", "config", {"levels_mapping": {"01": "L1", "02": "L2"}})
    api("PUT", "programs/AA-01-AA", {"fields": {"stacking": "01-02"}})
    run_sync()
    instances = api("GET", "programs/AA-01-AA")["program_volumes"]["instances"]
    check(len(instances) == 2, f"{len(instances)} volumes, expected 2")
    for instance in instances:
        fields = instance["fields"]
        check(
            fields.get("tenant_type") == "Retail" and fields.get("rent_per_sf") == 42.5,
            f"volume {instance['id']} on {instance['level']} has "
            f"{fields.get('tenant_type')!r}, {fields.get('rent_per_sf')!r}",
        )
    levels = sorted(instance["level"] for instance in instances)
    return f"2 volumes on {levels} carry tenant_type=Retail, rent_per_sf=42.5"


@step("S2")
def s2() -> str:
    reply = api(
        "PUT",
        "programs",
        {"filters": {"tenant_type": "Retail"}, "fields": {"tenant_type": "Office"}},
    )
    check(len(reply["updated"]) == 1, f"updated {reply['updated']}")
    run_sync()
    instances = api("GET", "programs/AA-01-AA")["program_volumes"]["instances"]
    values = [instance["fields"].get("tenant_type") for instance in instances]
    check(values == ["Office"] * 2, f"volume values {values}")
    return "update by filter, then sync: both volumes show tenant_type=Office"


@step("S3")
def s3() -> str:
    guid = next(item["guid"] for item in tracked() if item["field"] == "rent_per_sf")
    revit_exec(
        "from System import Guid\n"
        "from pyrevit import DB\n"
        f"element = DB.SharedParameterElement.Lookup(doc, Guid('{guid}'))\n"
        "t = DB.Transaction(doc, 'acceptance: delete parameter')\n"
        "t.Start()\n"
        "doc.Delete(element.Id)\n"
        "t.Commit()\n"
        "result = True\n"
    )
    item = next(item for item in tracked() if item["field"] == "rent_per_sf")
    check(item["bound"] is False, "the deleted parameter is still 'bound'")
    value = api("GET", "programs/AA-01-AA")["rent_per_sf"]
    check(value is None, f"rent_per_sf reads {value!r}, expected null")
    preflight = api("GET", "preflight")
    check(
        preflight["can_run_sync"] is False,
        "preflight permits the sync with a tracked parameter that is not in the model",
    )
    errors = [c["message"] for c in preflight["checks"] if c["level"] == "error"]
    check(
        any("tracked parameters missing" in message for message in errors),
        f"no preflight error for the missing parameter: {errors}",
    )
    api("DELETE", "parameters/rent_per_sf")
    preflight = api("GET", "preflight")
    check(preflight["can_run_sync"] is True, "preflight still stops the sync")
    run_sync()
    return (
        f"bound=false, value null, preflight error {errors!r}; untrack, then sync ran"
    )


# =============================================================================
# Model "empty2": own area schemes, batch placement, volume shape
# =============================================================================

UNITS = {
    "AA-01-AA": rect(0, 0, 20, 30),
    "AA-01-AB": rect(20, 0, 45, 30),
    "AA-01-AC": rect(45, 0, 60, 30),
}


@step("B1")
def b1() -> str:
    new_project("empty2")
    api("POST", "bootstrap", {"area_scheme_mappings": MAPPINGS_OWN_SCHEMES})
    names = [scheme["name"] for scheme in api("GET", "area-schemes")]
    made = [name for name in names if name.startswith("PDSO - ")]
    check(not made, f"the bootstrap made the schemes {made}")
    mappings = api("GET", "config")["area_scheme_mappings"]
    check(mappings == MAPPINGS_OWN_SCHEMES, f"mappings {mappings}")
    return f"schemes {sorted(names)}; mappings {mappings}"


@step("B2")
def b2() -> str:
    area_ids = {}
    for uid, outline in UNITS.items():
        area = api(
            "POST", "areas", {"scheme": "Rentable", "level": "L1", "points": outline}
        )
        area_ids[uid] = area["id"]
    STATE["unit_area_ids"] = area_ids
    items = [
        {"area_id": area_id, "fields": {"uid": uid, "display_name": uid}}
        for uid, area_id in area_ids.items()
    ]
    created = api("POST", "programs/batch", {"items": items})["created"]
    check(len(created) == 3, f"{len(created)} programs created, expected 3")
    for uid, area_id in area_ids.items():
        area = api("GET", f"programs/{uid}")["areas"].get("Rentable")
        check(
            area is not None and area["id"] == area_id,
            f"{uid} is not inside its Area {area_id}: {area}",
        )

    level_id = api("GET", "levels")[0]["id"]
    bad = failure_of(
        "POST",
        "programs/batch",
        {
            "items": [
                {"level": "L1", "x": 5, "y": 5, "fields": {"uid": "BB-01-AA"}},
                {"area_id": level_id, "fields": {"uid": "BB-01-AB"}},
            ]
        },
    )
    check(bad.status == 400, f"bad Area gave {bad.status}, expected 400")
    check(
        "items[1]" in bad.message and str(level_id) in bad.message,
        f"message does not name the item: {bad.message!r}",
    )
    duplicate = failure_of(
        "POST",
        "programs/batch",
        {
            "items": [
                {"level": "L1", "x": 5, "y": 5, "fields": {"uid": "CC-01-AA"}},
                {"level": "L1", "x": 6, "y": 6, "fields": {"uid": "CC-01-AA"}},
            ]
        },
    )
    check(duplicate.status == 400, f"duplicate UID gave {duplicate.status}")
    count = len(api("GET", "programs"))
    check(count == 3, f"{count} programs after the rejected batches, expected 3")
    return (
        f"3 programs inside Areas {sorted(area_ids.values())}; "
        f"bad item -> 400 {bad.message!r}; duplicate UID -> 400; still 3"
    )


def volume_extent(volume_id: int) -> list[float]:
    """XY bounding box [min x, min y, max x, max y] of an element, in feet."""
    return revit_exec(
        "from System import Int64\n"
        "from pyrevit import DB\n"
        f"element = doc.GetElement(DB.ElementId(Int64({volume_id})))\n"
        "box = element.get_BoundingBox(None)\n"
        "result = [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y]\n"
    )


def outline_extent(outline: list[list[float]]) -> list[float]:
    xs = [point[0] for point in outline]
    ys = [point[1] for point in outline]
    return [min(xs), min(ys), max(xs), max(ys)]


def same_extent(a: list[float], b: list[float], tolerance: float = 0.01) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


@step("V1")
def v1() -> str:
    gross = api(
        "POST",
        "areas",
        {"scheme": "Gross Building", "level": "L1", "points": rect(-5, -5, 65, 35)},
    )
    run_sync()
    program = api("GET", "programs/AA-01-AA")
    instances = program["program_volumes"]["instances"]
    check(len(instances) == 1, f"{len(instances)} volumes, expected 1")
    extent = volume_extent(instances[0]["id"])
    unit = outline_extent(UNITS["AA-01-AA"])
    check(
        same_extent(extent, unit),
        f"volume extent {extent} is not the Rentable outline {unit}",
    )
    rentable = program["areas"]["Rentable"]["area"]
    check(abs(program["nsf"] - rentable) < 0.01, f"nsf {program['nsf']} != {rentable}")
    check(
        abs(program["gsf"] - gross["area"]) < 0.01,
        f"gsf {program['gsf']} != {gross['area']}",
    )
    return (
        f"1 volume, extent {[round(v, 3) for v in extent]} = Rentable outline; "
        f"nsf={program['nsf']}, gsf={program['gsf']}"
    )


# =============================================================================
# Models "snowdon" and "sample": the acceptance of the design
# =============================================================================


def unit_scheme() -> tuple[dict[str, str], str]:
    """Pick the scheme mappings of the open model.

    Returns:
        (area_scheme_mappings, name of the scheme that has the unit Areas)
    """
    schemes = api("GET", "area-schemes")
    gross = [s for s in schemes if s["is_gross_building_area"] and s["area_count"]]
    other = sorted(
        (s for s in schemes if not s["is_gross_building_area"] and s["area_count"]),
        key=lambda s: -s["area_count"],
    )
    check(bool(gross or other), f"the model has no Areas: {schemes}")
    mappings = {}
    if other:
        mappings["PDSO - NSF"] = other[0]["name"]
        units = other[0]["name"]
        if gross:
            mappings["PDSO - GSF"] = gross[0]["name"]
    else:
        mappings["PDSO - GSF"] = gross[0]["name"]
        units = gross[0]["name"]
    return mappings, units


def unit_areas() -> list[dict]:
    areas = api("GET", "areas", scheme=STATE["units"])
    return [area for area in areas if area["placed"] and len(area["outline"]) >= 3]


@step("A1")
def a1() -> str:
    path = model_path(STATE["model"])
    api("POST", "document/open", {"path": path})
    document = api("GET", "status")["document"]
    check(
        document["path"].lower() == path.lower(),
        f"the active document is {document['path']}",
    )
    mappings, units = unit_scheme()
    STATE["mappings"], STATE["units"] = mappings, units
    report = api("POST", "bootstrap", {"area_scheme_mappings": mappings, "levels": []})
    names = [scheme["name"] for scheme in api("GET", "area-schemes")]
    made = [name for name in names if name.startswith("PDSO - ")]
    check(not made, f"the bootstrap made the schemes {made}")
    parameters = tracked()
    unbound = [item["name"] for item in parameters if not item["bound"]]
    check(len(parameters) >= 20 and not unbound, f"not bound: {unbound}")
    checks = api("GET", "config/checks")
    errors = [item for item in checks if item["level"] == "error"]
    check(not errors, f"config checks: {errors}")
    return (
        f"opened {document['title']}; mappings {mappings}; "
        f"shared parameters created: {len(report['shared_parameters']['created'])}; "
        f"family: {report['data_silo_family']}"
    )


@step("A2")
def a2() -> str:
    if "units" not in STATE:
        STATE["mappings"], STATE["units"] = unit_scheme()
    levels = {level["name"]: level for level in api("GET", "levels")}
    names = sorted(
        {area["level"] for area in unit_areas()},
        key=lambda name: levels[name]["elevation"],
    )
    all_names = [level["name"] for level in api("GET", "levels")]
    repeated = sorted({name for name in all_names if all_names.count(name) > 1})
    mapping = {
        f"{index + 1:02d}": levels[name]["id"] for index, name in enumerate(names)
    }
    STATE["level_keys"] = {name: f"{index + 1:02d}" for index, name in enumerate(names)}
    saved = api("PUT", "config", {"levels_mapping": mapping})["levels_mapping"]
    check(len(saved) == len(mapping), f"saved {len(saved)} of {len(mapping)} levels")
    return (
        f"levels_mapping {dict(zip(mapping, names))}; repeated level names: {repeated}"
    )


@step("A3")
def a3() -> str:
    before = len(tracked())
    ensure_custom_parameters()
    parameters = tracked()
    custom = [item for item in parameters if not item["standard"]]
    check(
        sorted(item["field"] for item in custom) == ["rent_per_sf", "tenant_type"],
        f"custom parameters {[item['field'] for item in custom]}",
    )
    check(all(item["bound"] for item in custom), f"not bound: {custom}")
    check(len(parameters) == 22, f"{len(parameters)} parameters, expected 22")
    return f"{before} -> 22 parameters; custom: {[item['name'] for item in custom]}"


@step("A4")
def a4() -> str:
    if "units" not in STATE:
        STATE["mappings"], STATE["units"] = unit_scheme()
    if "level_keys" not in STATE:
        a2()
    areas = unit_areas()
    all_areas = api("GET", "areas", scheme=STATE["units"], outline="false")
    counters: dict[str, int] = {}
    items = []
    for area in sorted(areas, key=lambda a: (a["level"], a["number"] or "")):
        key = STATE["level_keys"][area["level"]]
        counters[key] = counters.get(key, 0) + 1
        check(counters[key] < 100, f"more than 99 Areas on level {area['level']}")
        name = area["name"] or ""
        tenant = (
            "Circulation"
            if any(word in name for word in ("Stair", "Elevator", "Corridor"))
            else "Tenant"
        )
        items.append(
            {
                "area_id": area["id"],
                "fields": {
                    "uid": f"S{key[-1]}-{counters[key]:02d}-{key}",
                    "display_name": name,
                    "tenant_type": tenant,
                    "rent_per_sf": 0.0 if tenant == "Circulation" else 42.5,
                },
            }
        )
    created = api("POST", "programs/batch", {"items": items})["created"]
    check(len(created) == len(areas), f"created {len(created)} of {len(areas)}")
    programs = api("GET", "programs")
    check(len(programs) == len(areas), f"GET programs gives {len(programs)}")
    STATE["sample_uids"] = [item["fields"]["uid"] for item in items[:3]]
    return (
        f"scheme {STATE['units']!r}: {len(all_areas)} Areas, {len(areas)} with an "
        f"outline, {len(created)} programs created (design count for Snowdon: 87)"
    )


@step("A5")
def a5() -> str:
    preflight = api("GET", "preflight")
    errors = [item for item in preflight["checks"] if item["level"] == "error"]
    check(preflight["can_run_sync"] is True, f"preflight errors: {errors}")
    sync = run_sync()
    return (
        f"preflight: {preflight['error_count']} errors, "
        f"{preflight['warning_count']} warnings; sync phases {sync['phases']}"
    )


def sample_uids() -> list[str]:
    if "sample_uids" not in STATE:
        STATE["sample_uids"] = [p["uid"] for p in api("GET", "programs")[:3]]
    return STATE["sample_uids"]


@step("A6")
def a6() -> str:
    if "units" not in STATE:
        STATE["mappings"], STATE["units"] = unit_scheme()
    programs = api("GET", "programs")
    with_volume = 0
    for program in programs:
        details = api("GET", f"programs/{program['uid']}")
        if details["program_volumes"]["instances"]:
            with_volume += 1
    check(
        with_volume == len(programs),
        f"{with_volume} of {len(programs)} programs have a Program Volume",
    )
    lines = []
    for uid in sample_uids():
        program = api("GET", f"programs/{uid}")
        area = program["areas"][STATE["units"]]
        check(area is not None, f"{uid} is in no {STATE['units']} Area")
        check(
            abs(program["nsf"] - area["area"]) < 0.01,
            f"{uid}: nsf {program['nsf']} != Area {area['area']}",
        )
        instance = program["program_volumes"]["instances"][0]
        outline = api("GET", f"areas/{area['id']}")["outline"]
        extent = volume_extent(instance["id"])
        check(
            same_extent(extent, outline_extent(outline), 0.05),
            f"{uid}: volume extent {extent} != Area extent {outline_extent(outline)}",
        )
        fields = instance["fields"]
        check(
            fields.get("tenant_type") == program["tenant_type"]
            and fields.get("rent_per_sf") == program["rent_per_sf"],
            f"{uid}: volume has {fields.get('tenant_type')!r}, "
            f"{fields.get('rent_per_sf')!r}",
        )
        lines.append(
            f"{uid}: nsf={program['nsf']:.2f}=Area {area['id']}, footprint ok, "
            f"volume tenant_type={fields['tenant_type']!r}"
        )
    return f"{with_volume} Program Volumes; " + "; ".join(lines)


@step("A7")
def a7() -> str:
    reply = api(
        "PUT",
        "programs",
        {"filters": {"tenant_type": "Tenant"}, "fields": {"tenant_type": "Office"}},
    )
    check(len(reply["updated"]) > 0, "no program has tenant_type=Tenant")
    run_sync()
    uid = next(
        program["uid"] for program in api("GET", "programs", tenant_type="Office")
    )
    instances = api("GET", f"programs/{uid}")["program_volumes"]["instances"]
    values = [instance["fields"].get("tenant_type") for instance in instances]
    check(values and set(values) == {"Office"}, f"{uid}: volume values {values}")
    return f"{len(reply['updated'])} programs -> Office; volume of {uid} shows Office"


@step("A8")
def a8() -> str:
    if "units" not in STATE:
        STATE["mappings"], STATE["units"] = unit_scheme()
    level = api("GET", "programs")[0]["level"]
    view = api("POST", "area-plans", {"scheme": STATE["units"], "level": level})
    view_ref = view.get("id") or view.get("name")
    legend = api(
        "POST", "views/color-areas", {"view": view_ref, "color_by": "tenant_type"}
    )
    check(legend["areas_colored"] > 0, "no Area was colored")
    check(len(legend["legend"]) >= 2, f"legend has {len(legend['legend'])} values")
    image = api("POST", "views/export", {"view": view_ref, "pixel_width": 2000})
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{STATE['model']}_color_by_tenant_type.png"
    path.write_bytes(base64.b64decode(image["image_base64"]))
    values = [(item["value"], item["count"]) for item in legend["legend"]]
    return (
        f"{legend['areas_colored']} Areas colored on {level}; legend {values}; {path}"
    )


@step("A9")
def a9() -> str:
    title = api("GET", "status")["document"]["title"]
    check(title.startswith("acc_"), f"will not save {title}: not an acceptance copy")
    api("POST", "document/save", {})
    holder = model_path("empty")
    api("POST", "document/open", {"path": holder})
    api("POST", "document/close", {"title": title, "save": False})
    api("POST", "document/open", {"path": model_path(STATE["model"])})
    custom = [item for item in tracked() if not item["standard"]]
    check(
        sorted(item["field"] for item in custom) == ["rent_per_sf", "tenant_type"]
        and all(item["bound"] for item in custom),
        f"after reopen: {custom}",
    )
    value = api("GET", "programs", tenant_type="Office")
    check(len(value) > 0, "after reopen no program has tenant_type=Office")
    return f"saved, closed, reopened {title}: 2 custom parameters, {len(value)} Office"


@step("A10")
def a10() -> str:
    uid = api("GET", "programs", tenant_type="Office")[0]["uid"]
    api("DELETE", "parameters/tenant_type")
    check(len(tracked()) == 21, "expected 21 tracked parameters after delete")
    again = api("POST", "parameters", {"name": TENANT, "type": "string"})
    check(again["reattached"] is True, "the parameter was not attached again")
    value = api("GET", f"programs/{uid}")["tenant_type"]
    check(value == "Office", f"{uid}: value after reattach is {value!r}")
    purged = api("DELETE", "parameters/tenant_type", purge="true")
    check(purged["purged"] is True, f"purge: {purged}")
    names = [item["name"] for item in tracked()]
    check(TENANT not in names, "the parameter is still tracked")
    # Purge unbinds the parameter from Generic Models, so its values are gone
    api("POST", "parameters", {"name": TENANT, "type": "string"})
    value = api("GET", f"programs/{uid}")["tenant_type"]
    check(value in ("", None), f"{uid}: value after purge is {value!r}, expected empty")
    api("DELETE", "parameters/tenant_type", purge="true")
    return f"delete kept 'Office' on {uid}; create again ok; purge deleted the values"


# =============================================================================
# empty2, after V1: Areas from Areas, views and selection, orphans, the
# Unit Layout Catalogue and open documents
# =============================================================================


def area_size(area_id: int) -> float:
    return api("GET", f"areas/{area_id}")["area"]


@step("X1")
def x1() -> str:
    gross = MAPPINGS_OWN_SCHEMES["PDSO - GSF"]
    rentable = MAPPINGS_OWN_SCHEMES["PDSO - NSF"]
    source = STATE["unit_area_ids"]["AA-01-AA"]
    inset = api(
        "POST", "areas/derive", {"scheme": gross, "areas": [source], "offset": 0.5}
    )
    check(not inset["errors"], f"derive with offset: {inset['errors']}")
    made = inset["created"][0]
    expected = (20 - 1.0) * (30 - 1.0)
    check(abs(made["area"] - expected) < 0.5, f"inset Area is {made['area']} SF, expected {expected}")
    api("DELETE", f"areas/{made['id']}")
    copies = api(
        "POST",
        "areas/derive",
        {"scheme": gross, "from_scheme": rentable, "level": "L1", "offset": 0},
    )
    check(not copies["errors"], f"derive all: {copies['errors']}")
    check(len(copies["created"]) == 3, f"{len(copies['created'])} Areas derived, expected 3")
    for area in copies["created"]:
        check(
            abs(area["area"] - area_size(area["source_id"])) < 0.01,
            f"Area {area['id']} differs from its source {area['source_id']}",
        )
    STATE["unit_gross_ids"] = dict(
        (uid, next(a["id"] for a in copies["created"] if a["source_id"] == source_id))
        for uid, source_id in STATE["unit_area_ids"].items()
    )
    again = api(
        "POST", "areas/derive", {"scheme": gross, "areas": [source], "offset": 0}
    )
    check(again["errors"], "deriving onto an existing Area did not fail")
    return (
        f"0.5 ft inset {made['area']:.1f} SF (expected {expected}); 3 copies in "
        f"{gross} match their sources; a second copy is refused"
    )


@step("V2")
def v2() -> str:
    gross = MAPPINGS_OWN_SCHEMES["PDSO - GSF"]
    view = api("POST", "area-plans", {"scheme": gross, "level": "L1"})
    api("POST", "views/activate", {"view": str(view["id"])})
    ids = sorted(STATE["unit_gross_ids"].values())
    api("POST", "selection", {"ids": ids, "zoom": False})
    selected = sorted(item["id"] for item in api("GET", "selection"))
    check(selected == ids, f"selection {selected}, expected {ids}")
    api("POST", "selection", {"ids": [], "zoom": False})
    return f"activated {view['name']}; selected and read back {ids}"


@step("O1")
def o1() -> str:
    run_sync()
    orphans = api("GET", "program-volumes/orphans")
    check(orphans == [], f"orphans right after a sync: {orphans}")
    purged = api("POST", "program-volumes/purge-orphans", {})
    check(purged["deleted"] == [], f"purge deleted {purged['deleted']}")
    wrong = status_of("POST", "program-volumes/purge-orphans", {"families": [1]})
    check(wrong == 409, f"purging a non-orphan gave {wrong}, expected 409")
    return "no orphans after a sync; purge of a non-orphan refused (409)"


@step("C1")
def c1() -> str:
    source, target = "AA-01-AA", "AA-01-AB"
    placed = revit_exec(
        "from pyrevit import DB\n"
        "categories = [DB.BuiltInCategory.OST_Furniture, DB.BuiltInCategory.OST_Casework,"
        " DB.BuiltInCategory.OST_SpecialityEquipment]\n"
        "symbol = None\n"
        "for category in categories:\n"
        "    for candidate in DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)"
        ".OfCategory(category):\n"
        "        placement = candidate.Family.FamilyPlacementType\n"
        "        if placement == DB.FamilyPlacementType.OneLevelBased:\n"
        "            symbol = candidate\n"
        "            break\n"
        "    if symbol is not None:\n"
        "        break\n"
        "level = [l for l in DB.FilteredElementCollector(doc).OfClass(DB.Level) if l.Name == 'L1'][0]\n"
        "result = None\n"
        "if symbol is not None:\n"
        "    t = DB.Transaction(doc, 'acceptance: furnish a unit')\n"
        "    t.Start()\n"
        "    if not symbol.IsActive:\n"
        "        symbol.Activate()\n"
        "    for x, y in ((5.0, 8.0), (12.0, 20.0)):\n"
        "        doc.Create.NewFamilyInstance(DB.XYZ(x, y, 0), symbol, level,"
        " DB.Structure.StructuralType.NonStructural)\n"
        "    t.Commit()\n"
        "    result = symbol.Family.Name\n"
    )
    check(placed, "the template has no furniture, casework or equipment family to place")

    items = api("GET", "catalogue/unit-items", unit=source)["items"]
    check(len(items) == 2, f"{len(items)} items in {source}, expected 2")
    harvest = api("POST", "catalogue/harvest", {"unit": source, "project_type": "Other"})
    layout_id = harvest["layout"]["id"]
    try:
        layout = api("GET", f"catalogue/layouts/{urllib.parse.quote(layout_id, safe='')}")
        check(len(layout["entry"]["items"]) == 2, "the harvested layout does not have 2 items")
        check(harvest["catalogue_number"] >= 1, "no catalogue number")

        similar = api("POST", "catalogue/similar-units", {"layout_id": layout_id})
        check(similar["units_checked"] >= 3, f"{similar['units_checked']} units checked")
        old = api("POST", "catalogue/similar-rooms", {"layout_id": layout_id})
        check(old["rooms_checked"] == similar["units_checked"], "the older route differs")

        recommended = api("POST", "catalogue/recommend", {"unit": target})
        check(
            any(item["layout_id"] == layout_id for item in recommended["recommendations"]),
            f"{layout_id} is not recommended for {target}",
        )
        preview = api("GET", "catalogue/preview", unit=target, layout_id=layout_id)
        check(preview["unit"]["id"] == preview["room"]["id"], "preview keys differ")

        result = api(
            "POST",
            "catalogue/place",
            {"unit": target, "layout_id": layout_id, "clear_first": True},
        )
        check(len(result["placed"]) == 2, f"{len(result['placed'])} items placed, expected 2")
        after = api("GET", "catalogue/room-items", room=target)["items"]
        check(len(after) == 2, f"{len(after)} items in {target} after the pull")
        cleared = api("DELETE", "catalogue/unit-items", unit=target)
        check(cleared["deleted"] == 2, f"cleared {cleared['deleted']}, expected 2")

        room = status_of("POST", "catalogue/recommend", {"unit": "304"})
        check(room == 404, f"a Room number gave {room}, expected 404")
    finally:
        api("DELETE", f"catalogue/layouts/{urllib.parse.quote(layout_id, safe='')}")
    return (
        f"furnished {source} with 2 {placed}; harvested {layout_id}; similar, "
        f"recommend, preview, place with clear_first, list and clear in {target}; "
        "older names answer; Room number refused"
    )


@step("D1")
def d1() -> str:
    active = api("GET", "status")["document"]["title"]
    background = api("POST", "documents/open", {"path": model_path("empty")})
    title = background["title"]
    try:
        documents = {item["title"]: item for item in api("GET", "documents")}
        check(documents[active]["is_active"], f"{active} is not the active document")
        check(not documents[title]["is_active"], f"{title} is active")
        levels = api("GET", "levels", document=title)
        check(levels, f"no levels read from {title}")
        wrong = status_of("POST", "selection", {"ids": [], "document": title})
        check(wrong == 409, f"selecting in a background document gave {wrong}")
        closing_active = status_of("POST", "documents/close", {"document": active})
        check(closing_active == 409, f"closing the active document gave {closing_active}")
    finally:
        closed = api("POST", "documents/close", {"document": title, "save": False})
    check(closed["closed"] == title, f"closed {closed}")
    return (
        f"opened {title} in the background, read its levels, selection there "
        "refused (409), active document not closable, closed it"
    )


# =============================================================================
# Runner
# =============================================================================


def expand(spec: str) -> list[str]:
    """ "R0,P1-P3,A1-A10" -> step ids."""
    ids = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            prefix = start.rstrip("0123456789")
            first, last = int(start[len(prefix) :]), int(end[len(prefix) :])
            ids.extend(f"{prefix}{number}" for number in range(first, last + 1))
        elif part:
            ids.append(part)
    unknown = [step_id for step_id in ids if step_id not in STEPS]
    if unknown:
        raise SystemExit(f"Unknown steps: {unknown}. Steps: {', '.join(STEPS)}")
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", required=True, choices=sorted(MODEL_FILES))
    parser.add_argument("--steps", required=True)
    parser.add_argument("--save-baseline", action="store_true")
    args = parser.parse_args()
    STATE["model"] = args.model
    STATE["save_baseline"] = args.save_baseline

    failed = False
    for step_id in expand(args.steps):
        try:
            evidence = STEPS[step_id]()
            print(f"STEP {step_id} PASS {evidence}", flush=True)
        except (StepFailure, ApiFailure) as failure:
            failed = True
            print(f"STEP {step_id} FAIL {failure}", flush=True)
        except Exception:
            failed = True
            print(f"STEP {step_id} FAIL {traceback.format_exc()}", flush=True)
        if failed:
            break
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
