# Driving PDSO through the pyRevit MCP

How an agent (Claude Code, Codex, Cursor, ...) can inspect and edit a PDSO model in a running Revit session through the **pyRevit agent runtime**. Read [AGENTS.md](../../AGENTS.md) and [architecture.md](architecture.md) first for the domain model.

> Status, checked 2026-11-14: the runtime shipped in **pyRevit 7.0** (October 2026) and is **experimental and off by default**. Facts below come from the [pyRevit agent runtime docs](https://docs.pyrevitlabs.io/agent-runtime/). Items marked *unverified* haven't been tested against a PDSO model yet. Confirm them on first use and update this file.

## Setup (once per workstation)

```bash
pyrevit configs agent enable
```

```bash
pyrevit mcp install claude
```

```bash
pyrevit agent status
```

- Reload pyRevit or restart Revit after `enable`.
- `install` takes `claude`, `codex`, `cursor`, `vscode` or `opencode`. Add `--project` to register for this repo only, which writes `.mcp.json`.
- Transport: the client starts `pyrevit mcp` (stdio), which talks to Revit over a named pipe restricted to the current Windows user. There's no TCP port.
- Approval policy: `pyrevit configs agent policy ask` (the default) asks for approval in Revit for every modify run. `readonly` allows queries and dry runs only. `auto` commits without asking. Use `ask` on real projects.
- With several Revit sessions open, pass `revit="2026"` (year or process id) on each tool call.

## Tools and what to use them for

| Tool | Use it in PDSO work to |
| --- | --- |
| `get_context` | Confirm the active document is a **project** (PDSO commands need one), see the engine (`scripting`), levels, selection and active view. Call it first. |
| `run_query` | Read anything: config, Data Silos, Program Volumes, health checks. Changes are always rolled back. |
| `run_modify` | Change Data Silo values. Always do a `dry_run=true` pass first. |
| `inspect_elements` | Dump the parameters of up to 50 elements (Data Silos, Areas, Program Volumes) by id. |
| `show_elements` | Select or isolate problem elements for the user (no model change). |
| `capture_view` | Screenshot an area plan or 3D view to check Program Volume shapes. |
| `lookup_revit_api` | Check Revit API signatures for the running Revit version (for example, `ElementId.Value` vs `IntegerValue`). |
| `get_run` | Fetch the record of an earlier run. |

There is **no tool to run a pushbutton yet.** `list_commands`/`run_command` and the `agent:` block in `bundle.yaml` are "Phase 3, planned". **Model Setup, Project Config and Sync Data can't be triggered by an agent today**; ask the user to click them. See [Running the sync](#running-the-sync).

## Script contract

The runtime injects `doc`, `uidoc`, `app`, `uiapp`, `DB`, `UI` and `inputs` (a dict). Assign `result` to return JSON-serializable data; printed output is returned too.

- Engine: IronPython by default (2.7.12 or 3.4.2, whichever pyRevit is attached to). Read `get_context().scripting` instead of guessing. Write the recipes below in syntax both versions accept: no f-strings, use `.format()`.
- `run_query`: `timeout_s` defaults to 300. `workspace=<folder>` puts that folder on `sys.path` and re-imports it fresh on every run.
- `run_modify`: **the script opens and commits its own `DB.Transaction`.** The runtime wraps the whole run in a `TransactionGroup` named `Agent: <title>`, so it shows up as one undo entry. A transaction left open fails the run.
- Changing another open document fails the run. Family documents opened with `EditFamily` are allowed, because they have no file.
- Revit dialogs are closed automatically and listed in the result.
- Return ids with `ElementId.Value` (Revit 2024+). `IntegerValue` was removed in later versions.

## Using PDSO's own library (optional)

The recipes below use only the Revit API, so they work without importing PDSO code. That's the reliable default. To reuse PDSO classes instead (`ExtendedDataSiloCollection`, `PDSOCorrelationService`, ...), make its `lib` importable:

```python
import sys
PDSO_LIB = inputs.get("pdso_lib")  # e.g. r"C:\...\pdso-flowgram\PDSO.extension\lib"
if PDSO_LIB and PDSO_LIB not in sys.path:
    sys.path.append(PDSO_LIB)
```

Or pass `workspace=<repo>/PDSO.extension/lib` to `run_query`.

That one folder covers `pdso`, `common` and the vendored `extensible_storage` library. The most useful import is `from pdso.code.business.parameter_registry_service import get_registry`.

*Unverified:*

- Whether pyRevit puts extension `lib` folders on `sys.path` for agent runs.
- Whether PDSO classes work outside a command context. They call `script.get_output()` in `__init__`, which assumes a pyRevit output window.

If any of these fail, fall back to the raw-API recipes.

## Recipes

Each block is a complete script for `run_query` unless it says otherwise.

### 1. Read Project Config (extensible storage)

```python
from System import Guid, String, Double, Boolean
from System.Collections.Generic import IDictionary
ES = DB.ExtensibleStorage

CONFIG_SCHEMA = Guid("7b533d48-f078-48ad-a393-48d73b71e96b")
SIMPLE = {"System.String": String, "System.Boolean": Boolean}

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

def read_field(ent, f):
    name, vt = f.FieldName, f.ValueType.FullName
    if f.ContainerType == ES.ContainerType.Map:
        if vt == "Autodesk.Revit.DB.ElementId":
            d = ent.Get[IDictionary[String, DB.ElementId]](name)
            return dict((k, eid(d[k])) for k in d.Keys)
        if vt == "System.Guid":
            d = ent.Get[IDictionary[String, Guid]](name)
            return dict((k, str(d[k])) for k in d.Keys)
        d = ent.Get[IDictionary[String, String]](name)
        return dict((k, d[k]) for k in d.Keys)
    if vt == "System.Double":
        try:
            return ent.Get[Double](name, DB.UnitTypeId.Feet)
        except Exception:
            return ent.Get[Double](name)
    if vt in SIMPLE:
        return ent.Get[SIMPLE[vt]](name)
    return "<{} not decoded>".format(vt)

schema = ES.Schema.Lookup(CONFIG_SCHEMA)
ent = doc.ProjectInformation.GetEntity(schema) if schema else None
if ent is None or not ent.IsValid():
    result = {"configured": False, "hint": "Ask the user to open Project Config and Save."}
else:
    cfg = {"configured": True}
    for f in schema.ListFields():
        try:
            cfg[f.FieldName] = read_field(ent, f)
        except Exception as ex:
            cfg[f.FieldName] = "<error: {}>".format(ex)
    result = cfg
```

Fields to expect:

- `data_silo_family_name`, `program_volume_family_name_prefix`
- `area_scheme_mappings`, `levels_mapping`, `uid_pattern`, `program_volume_extrusion_height`, `worksets`, `last_run_timestamp`
- `tethered_parameters`: the **tracked parameter list** (`{name: guid}`). It's empty if the Parameter Manager has never been applied, and then Sync Data is blocked.

The other recipes take `family_name`, `prefix` and `params` (the keys of `tethered_parameters`) from this result, through `inputs`.

### 2. List Data Silos and their data

`inputs`: `{"family_name": "<data_silo_family_name>", "params": [<tracked names from recipe 1>]}`. If `params` is omitted, the script falls back to the built-in names.

```python
BUILT_IN = [
    "PDSO - Program UID", "PDSO - Program Display Name", "PDSO - Unit Layout Type",
    "PDSO - Program Type", "PDSO - Use Group", "PDSO - Planning Dept. Misc",
    "PDSO - Accessibility", "PDSO - Program Stacking", "PDSO - Program Market Segment",
    "PDSO - Misc Modifier", "PDSO - Load Factor", "PDSO - DU Bedroom Count",
    "PDSO - Bathroom Count", "PDSO - Bathroom Types", "PDSO - Kitchen Count",
    "PDSO - Kitchen Types", "PDSO - ZFA", "PDSO - NSF", "PDSO - GSF", "PDSO - Last Synced",
]
PDSO_PARAMS = inputs.get("params") or BUILT_IN

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

def value(p):
    if p is None or not p.HasValue:
        return None
    st = p.StorageType
    if st == DB.StorageType.String:
        return p.AsString()
    if st == DB.StorageType.Integer:
        return p.AsInteger()  # Integer and Yes/No custom parameters
    if st == DB.StorageType.Double:
        return p.AsValueString()  # display units, e.g. "1040 SF"
    return p.AsValueString()

fam = inputs["family_name"]
rows = []
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    if fi.Symbol.Family.Name != fam:
        continue
    level = doc.GetElement(fi.LevelId)
    row = {"id": eid(fi.Id), "level": level.Name if level else None}
    for name in PDSO_PARAMS:
        row[name] = value(fi.LookupParameter(name))
    rows.append(row)
result = {"count": len(rows), "data_silos": rows}
```

### 3. Health check (no sync needed)

Builds on recipe 2. Add this after building `rows`:

```python
import re
from collections import Counter

uids = Counter(r["PDSO - Program UID"] for r in rows)
pattern = inputs.get("uid_pattern") or ""
result = {
    "duplicate_uids": [u for u, n in uids.items() if n > 1],
    "missing_uid": [r["id"] for r in rows if not r["PDSO - Program UID"]],
    "bad_uid_pattern": [r["id"] for r in rows
                        if pattern and r["PDSO - Program UID"]
                        and not re.match(pattern, r["PDSO - Program UID"])],
    "missing_gsf": [r["id"] for r in rows if not r["PDSO - GSF"]],
    "never_synced": [r["id"] for r in rows if not r["PDSO - Last Synced"]],
}
```

This doesn't replace preflight or Audit Report, which also check Area containment, level mapping and boundaries. It's a quick triage before you ask the user to run them.

### 4. Program Volumes per Data Silo

`inputs`: `{"prefix": "<program_volume_family_name_prefix>"}`

```python
from collections import defaultdict

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

prefix = inputs["prefix"]
by_family = defaultdict(list)
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    fname = fi.Symbol.Family.Name
    if fname.startswith(prefix):
        lvl = doc.GetElement(fi.LevelId)
        by_family[fname[len(prefix):]].append({"id": eid(fi.Id), "level": lvl.Name if lvl else None})
result = {"uids": dict(by_family), "family_count": len(by_family)}
```

Compare with recipe 2 to find orphans: a UID here with no Data Silo means a renamed or deleted unit. Compare instance counts with `1 + stacked levels` to find stacking that hasn't been synced.

### 5. Edit Data Silo values (`run_modify`)

`inputs`: `{"family_name": "...", "tracked": [<tracked names>], "area_params": [<area_scheme_mappings keys and values>], "updates": {"00-07-01": {"PDSO - DU Bedroom Count": 2, "PDSO - Unit Layout Type": "2BR - Type A"}}}`

```python
fam, updates = inputs["family_name"], inputs["updates"]
tracked = set(inputs["tracked"])
# Sync-owned: harvested SF parameters and Last Synced. Pass both sides of
# area_scheme_mappings, since the code reads that map in both directions.
PROTECTED = set(inputs.get("area_params") or []) | {
    "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA", "PDSO - Last Synced"}

bad = [n for u in updates.values() for n in u if n in PROTECTED or n not in tracked]
if bad:
    raise Exception("Refusing to write sync-owned or untracked parameters: {}".format(bad))

silos = {}
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    if fi.Symbol.Family.Name == fam:
        p = fi.LookupParameter("PDSO - Program UID")
        if p and p.AsString():
            silos[p.AsString()] = fi

changed, missing = [], []
t = DB.Transaction(doc, "PDSO - Agent edit Data Silos")
t.Start()
try:
    for uid, params in updates.items():
        fi = silos.get(uid)
        if fi is None:
            missing.append(uid)
            continue
        for name, val in params.items():
            p = fi.LookupParameter(name)
            if p is None or p.IsReadOnly:
                missing.append("{}:{}".format(uid, name))
                continue
            st = p.StorageType
            if st == DB.StorageType.Double:
                ok = p.Set(float(val))
            elif st == DB.StorageType.Integer:
                ok = p.Set(int(val))
            else:
                ok = p.Set(u"{}".format(val))
            (changed if ok else missing).append("{}:{}".format(uid, name))
    t.Commit()
except Exception:
    t.RollBack()
    raise
result = {"changed": changed, "skipped": missing}
```

Run it with `dry_run=true` first and show the user the change set. The values only reach Program Volumes after the user runs **Sync Data**.

## Rules for agents

- **Write only to Data Silos**, and only **tracked**, user-owned parameters. Never write Program Volumes, the harvested SF parameters, `PDSO - Last Synced`, or Area parameters.
- **Don't change which parameters are tracked** from a script. That's the Parameter Manager's job (`PDSOParameterSetupService.apply`), and untracking deletes values.
- **Don't change Program UIDs** without telling the user it orphans Program Volumes (see architecture known gaps).
- **Don't write extensible storage config** (Project Config) from scripts. It's per-project setup, and wrong values orphan elements. Ask the user to change it in Project Config.
- **Don't create or delete Areas or Data Silos** unless the user asked for that exact change.
- In workshared models, elements owned by someone else fail to edit. Report them; don't retry.
- Model data is sent to the AI provider, and run records are kept in plaintext under `%APPDATA%\pyRevit\agent\runs` for 14 days. Don't run this on projects whose data can't leave the machine.

## Running the sync

Today an agent **can't** start Sync Data:

1. There's no `run_command` tool yet.
2. The sync logic lives in the WPF view model (`view_models/body_2_harvest_areas.py`), not in a callable function.

Ask the user to click **Sync Data** and then verify with recipes 2–4.

**Making it callable** is the integration work for the hackathon:

1. Extract `run_preflight(doc) -> dict` and `run_sync(doc, options) -> dict` from the view model into `lib/pdso/code/business/` (for example, `sync_service.py`). They should take an injected `doc`, use no UI properties, and return the check results and per-phase counts. The view model then calls them, so the button behaves the same.
2. Make the classes they use safe without a pyRevit output window: create `script.get_output()` lazily or let callers inject it.
3. Call it from `run_modify` with `sys.path` pointing to `PDSO.extension/lib`. Phase 2 opens family documents with `EditFamily` and loads them back, which the runtime allows. *Verify with `dry_run` on a test model first.*
4. When pyRevit's Phase 3 ships, add an `agent:` block (`expose: true`) to the Sync Data and Audit Report `bundle.yaml` files so agents can call them as commands.
