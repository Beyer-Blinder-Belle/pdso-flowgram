# -*- coding: utf-8 -*-
"""PDSO Config Service - read and write the model configuration as plain data.

The configuration is in Extensible Storage on ProjectInfo (see
PdsoConfigDocumentSchema). This service converts it to and from plain dicts and
runs the model checks of the Project Config window.

Example Usage:
    config = get_config(doc)
    update_config(doc, {"program_volume_extrusion_height": 10.0})
"""

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from ..data.shared_parameter_constants import standard_pdso_parameters
from .errors import PDSOError
from . import parameter_registry_service
from .extensible_storage_service import PDSOExtensibleStorageService
from .revit_helpers import find_level, id_value

logger = script.get_logger()

SIMPLE_FIELDS = [
    "version",
    "data_silo_family_name",
    "uid_pattern",
    "program_volume_family_name_prefix",
    "program_volume_extrusion_height",
]

WORKSET_KEYS = ["data-silos", "program-volumes", "areas"]


def _to_dict(net_dictionary):
    """Convert a .NET dictionary (or None) to a Python dict."""
    if not net_dictionary:
        return {}
    if hasattr(net_dictionary, "Keys"):
        return dict((key, net_dictionary[key]) for key in net_dictionary.Keys)
    return dict(net_dictionary)


def config_exists(doc):
    """True if the document has a PDSO configuration."""
    return PDSOExtensibleStorageService(doc).check_document_pdso_config_schema_exists()


def initialize_config(doc):
    """Write the default configuration if the document has none.

    Returns:
        bool: True if a new configuration was written
    """
    service = PDSOExtensibleStorageService(doc)
    if service.check_document_pdso_config_schema_exists():
        return False
    if not service.initialize_document_schema_with_defaults():
        raise PDSOError("Could not write the default PDSO configuration.", 500)
    return True


def get_config(doc):
    """Read the configuration as a plain dict.

    Returns:
        dict: {"exists": False} if the document has no configuration. Otherwise
            the fields of PdsoConfigDocumentSchema. "levels_mapping" maps each
            key to {"level_id", "level_name"}.
    """
    service = PDSOExtensibleStorageService(doc)
    if not service.check_document_pdso_config_schema_exists():
        return {"exists": False}

    config = service.get_document_config()
    if config is None:
        return {"exists": False}

    result = {"exists": True}
    for field in SIMPLE_FIELDS:
        result[field] = getattr(config, field)

    result["area_scheme_mappings"] = _to_dict(config.area_scheme_mappings)
    result["worksets"] = _to_dict(config.worksets)
    result["last_run_timestamp"] = _to_dict(config.last_run_timestamp)
    result["tethered_parameters"] = dict(
        (name, str(guid)) for name, guid in _to_dict(config.tethered_parameters).items()
    )

    levels = {}
    for key, level_id in _to_dict(config.levels_mapping).items():
        level = doc.GetElement(level_id)
        levels[key] = {
            "level_id": id_value(level_id),
            "level_name": level.Name if level is not None else None,
        }
    result["levels_mapping"] = levels
    return result


def update_config(doc, updates):
    """Change configuration fields. Must be called with no open transaction.

    Args:
        doc: Revit Document
        updates (dict): Fields to change. Fields that are not given keep their
            value. Map fields (area_scheme_mappings, levels_mapping, worksets)
            are replaced as a whole and cannot be empty.
            "levels_mapping" maps each stacking key (for example "01") to a level
            name or level id. The "PDSO - Program Stacking" parameter of a Data
            Silo uses these keys ("01-02" or "01, 03"). The tracked parameters
            ("tethered_parameters") are not changed here: see parameter_service.

    Returns:
        dict: The configuration after the change (see get_config)
    """
    if not isinstance(updates, dict) or not updates:
        raise PDSOError("Give at least one configuration field to change.")

    permitted = SIMPLE_FIELDS[1:] + [
        "area_scheme_mappings",
        "levels_mapping",
        "worksets",
    ]
    if "tethered_parameters" in updates:
        raise PDSOError(
            "tethered_parameters holds the tracked parameters. "
            "Use POST /pdso/parameters and DELETE /pdso/parameters/<name>."
        )
    unknown = [name for name in updates if name not in permitted]
    if unknown:
        raise PDSOError(
            "Unknown configuration field(s): {}. Permitted: {}".format(
                ", ".join(sorted(unknown)), ", ".join(permitted)
            )
        )

    values = {}
    for name in (
        "data_silo_family_name",
        "uid_pattern",
        "program_volume_family_name_prefix",
    ):
        if name in updates:
            values[name] = str(updates[name])

    if "program_volume_extrusion_height" in updates:
        height = float(updates["program_volume_extrusion_height"])
        if height <= 0:
            raise PDSOError("program_volume_extrusion_height must be more than 0.")
        values["program_volume_extrusion_height"] = height

    if "area_scheme_mappings" in updates:
        mappings = updates["area_scheme_mappings"]
        _require_map(mappings, "area_scheme_mappings")
        tracked = parameter_registry_service.get_registry(doc, refresh=True)
        for parameter_name in mappings:
            if (
                parameter_name not in standard_pdso_parameters
                and parameter_name not in tracked
            ):
                raise PDSOError(
                    "area_scheme_mappings key '{}' is not a PDSO parameter.".format(
                        parameter_name
                    )
                )
        values["area_scheme_mappings"] = dict(
            (str(key), str(value)) for key, value in mappings.items()
        )

    if "worksets" in updates:
        worksets = updates["worksets"]
        _require_map(worksets, "worksets")
        values["worksets"] = dict(
            (str(key), str(value)) for key, value in worksets.items()
        )

    if "levels_mapping" in updates:
        mapping = updates["levels_mapping"]
        _require_map(mapping, "levels_mapping")
        values["levels_mapping"] = dict(
            (str(key), find_level(doc, reference).Id)
            for key, reference in mapping.items()
        )

    initialize_config(doc)
    service = PDSOExtensibleStorageService(doc)
    if not service.update_document_config(**values):
        raise PDSOError("Could not save the PDSO configuration.", 500)
    return get_config(doc)


def _require_map(value, name):
    if not isinstance(value, dict) or not value:
        raise PDSOError("{} must be an object with at least one entry.".format(name))


def run_model_checks(doc):
    """Run the model checks of the Project Config window.

    Returns:
        list[dict]: One dict per check with keys "key", "level" ("ok", "warning"
            or "error") and "message"
    """
    checks = []

    def add(key, level, message):
        checks.append({"key": key, "level": level, "message": message})

    config = get_config(doc)
    if config["exists"]:
        add("schema", "ok", "Schema loaded and config saved")
    else:
        add("schema", "error", "Schema not initialized")

    family_name = config.get("data_silo_family_name")
    if not family_name:
        add("data_silo_family", "warning", "No Data Silo family name configured")
        add("data_silo_instances", "warning", "No Data Silo family name configured")
    else:
        families = DB.FilteredElementCollector(doc).OfClass(DB.Family)
        if any(family.Name == family_name for family in families):
            add("data_silo_family", "ok", "Family '{}' is loaded".format(family_name))
        else:
            add(
                "data_silo_family",
                "error",
                "Family '{}' is not loaded".format(family_name),
            )
        instances = [
            instance
            for instance in DB.FilteredElementCollector(doc)
            .OfClass(DB.FamilyInstance)
            .OfCategory(DB.BuiltInCategory.OST_GenericModel)
            if instance.Symbol.Family.Name == family_name
        ]
        if instances:
            add("data_silo_instances", "ok", "{} Data Silos".format(len(instances)))
        else:
            add("data_silo_instances", "warning", "No Data Silo instances placed")

    binding_check = parameter_registry_service.check_parameter_bindings(doc)
    if not binding_check["configured"]:
        add(
            "shared_parameters",
            "error",
            "Parameters not set up. Run the bootstrap or the Parameter Manager.",
        )
    elif binding_check["missing_core"]:
        add(
            "shared_parameters",
            "error",
            "Required parameters not tracked: {}".format(
                ", ".join(binding_check["missing_core"])
            ),
        )
    elif binding_check["missing"]:
        add(
            "shared_parameters",
            "error",
            "Tracked parameters missing on Generic Models: {}".format(
                ", ".join(binding_check["missing"])
            ),
        )
    else:
        add(
            "shared_parameters",
            "ok",
            "All {} tracked parameters present".format(len(binding_check["found"])),
        )

    scheme_names = list(config.get("area_scheme_mappings", {}).values())
    existing = set(
        scheme.Name
        for scheme in DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme)
    )
    missing_schemes = sorted(name for name in scheme_names if name not in existing)
    if not scheme_names:
        add("area_schemes", "warning", "No area schemes configured")
    elif missing_schemes:
        add(
            "area_schemes",
            "error",
            "Missing {} scheme(s): {}".format(
                len(missing_schemes), ", ".join(missing_schemes)
            ),
        )
    else:
        add(
            "area_schemes",
            "ok",
            "All {} configured area schemes exist".format(len(scheme_names)),
        )

    counts = {}
    areas = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Areas)
    for area in areas:
        try:
            name = area.AreaScheme.Name
        except Exception:
            continue
        counts[name] = counts.get(name, 0) + 1
    if counts:
        add(
            "area_instances",
            "ok",
            "Area counts: "
            + ", ".join("{}: {}".format(name, counts[name]) for name in sorted(counts)),
        )
    else:
        add("area_instances", "warning", "No areas found in any scheme")

    return checks
