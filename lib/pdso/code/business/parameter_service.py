# -*- coding: utf-8 -*-
"""PDSO Parameter Service - the tracked parameters as plain data, for the API.

A model tracks a list of parameters (see parameter_registry_service). The sync
copies each tracked parameter from a Data Silo to its Program Volumes. This
service is the UI-free way to read that list and to change it: it does what
the Parameter Manager window does, one parameter at a time.

It also gives each tracked parameter a short field name for the API
("PDSO - Program UID" -> "uid", "Rent per SF" -> "rent_per_sf").

Example Usage:
    setup_default(doc)                               # track the built-in parameters
    create_parameter(doc, "Tenant Type", "string")   # add a custom parameter
    delete_parameter(doc, "tenant_type")             # stop tracking it
"""

# Python standard libraries
import os
import re
import tempfile

# 3rd Party Modules
from pyrevit import script, DB

# Custom Modules
from ..data.extended_shared_parameter import (
    PARAMETER_TYPE_ORDER,
    ROLE_AREA,
    ROLE_CUSTOM,
    ExtendedSharedParameter,
    parameter_type_from_spec,
)
from ..data.shared_parameter_constants import (
    CORE_PARAMETER_NAMES,
    LAST_SYNCED,
    standard_pdso_parameters,
)
from . import parameter_registry_service as registry_service
from .errors import PDSOError

logger = script.get_logger()

PREFIX = "PDSO - "

# Short field name of each built-in parameter
STANDARD_FIELDS = {
    "PDSO - Program UID": "uid",
    "PDSO - Program Display Name": "display_name",
    "PDSO - Unit Layout Type": "unit_layout_type",
    "PDSO - Program Type": "program_type",
    "PDSO - Use Group": "use_group",
    "PDSO - Planning Dept. Misc": "planning_dept_misc",
    "PDSO - Accessibility": "accessibility",
    "PDSO - Program Stacking": "stacking",
    "PDSO - Program Market Segment": "market_segment",
    "PDSO - Misc Modifier": "misc_modifier",
    "PDSO - Load Factor": "load_factor",
    "PDSO - DU Bedroom Count": "bedroom_count",
    "PDSO - Bathroom Count": "bathroom_count",
    "PDSO - Bathroom Types": "bathroom_types",
    "PDSO - Kitchen Count": "kitchen_count",
    "PDSO - Kitchen Types": "kitchen_types",
    "PDSO - ZFA": "zfa",
    "PDSO - NSF": "nsf",
    "PDSO - GSF": "gsf",
    "PDSO - Last Synced": "last_synced",
}


# =============================================================================
# Fields: the tracked parameters with their API field names
# =============================================================================


def slug(name):
    """Field name of a parameter name ("PDSO - Rent per SF" -> "rent_per_sf")."""
    text = (name or "").strip()
    if text.startswith(PREFIX):
        text = text[len(PREFIX):]
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def field_name(name):
    """Field name of a parameter: the short name of a built-in, or the slug."""
    return STANDARD_FIELDS.get(name) or slug(name) or "parameter"


class Field(object):
    """One tracked parameter, as the API sees it.

    Attributes:
        definition (ExtendedSharedParameter): Name, type, GUID and role
        field (str): Field name for the API
        standard (bool): True for a built-in PDSO parameter
        managed (bool): True if the sync writes the value (a caller cannot)
        missing (bool): True if the model has no usable Revit parameter for it
    """

    def __init__(self, definition, field):
        self.definition = definition
        self.field = field
        self.standard = definition.parameter_name in standard_pdso_parameters
        self.managed = (
            definition.role == ROLE_AREA or definition.parameter_name == LAST_SYNCED
        )
        self.missing = (
            definition.parameter_type is None or definition.parameter_guid is None
        )

    @property
    def name(self):
        return self.definition.parameter_name

    @property
    def parameter_type(self):
        return self.definition.parameter_type

    @property
    def guid(self):
        return self.definition.parameter_guid

    @property
    def role(self):
        return self.definition.role


def all_parameters(doc, include_missing=False):
    """The tracked parameters of the model, in the order of the registry.

    Two parameters cannot have the same field name: the second one gets a
    number ("rent", "rent_2").
    """
    registry = registry_service.get_registry(doc, refresh=True)
    fields = []
    used = set()
    for definition in registry.parameters:
        base = field_name(definition.parameter_name)
        name = base
        number = 2
        while name in used:
            name = "{}_{}".format(base, number)
            number += 1
        used.add(name)
        fields.append(Field(definition, name))
    if not include_missing:
        fields = [field for field in fields if not field.missing]
    return fields


def by_field(doc):
    """Field name -> Field (missing parameters included)."""
    return dict((field.field, field) for field in all_parameters(doc, True))


# =============================================================================
# Read
# =============================================================================


def _info(field, problems, tracked=True):
    return {
        "name": field.name,
        "field": field.field,
        "type": field.parameter_type,
        "guid": str(field.guid) if field.guid is not None else None,
        "role": field.role,
        "standard": field.standard,
        "tracked": tracked,
        "editable": tracked and not field.managed and not field.missing,
        "bound": tracked and field.name not in problems,
        "problem": problems.get(field.name) if tracked else None,
    }


def list_parameters(doc):
    """List the parameters of the model.

    Returns:
        dict: "configured" (False until the parameters are set up; the sync
            cannot run then), "types" (permitted types of a new parameter) and
            "parameters": one dict per parameter with keys "name", "field",
            "type", "guid", "role" ("core", "area", "standard" or "custom"),
            "standard" (a built-in parameter), "tracked", "editable", "bound"
            (it is on Generic Models as an instance parameter) and "problem".
            The tracked parameters come first. A built-in parameter that the
            model does not track has "tracked": False.
    """
    registry = registry_service.get_registry(doc, refresh=True)
    check = registry_service.check_parameter_bindings(doc, registry)
    parameters = [_info(field, check["problems"]) for field in all_parameters(doc, True)]
    for name, definition in standard_pdso_parameters.items():
        if name not in registry:
            parameters.append(
                _info(Field(definition, field_name(name)), {}, tracked=False)
            )
    return {
        "configured": registry.is_configured,
        "types": list(PARAMETER_TYPE_ORDER),
        "parameters": parameters,
    }


# =============================================================================
# Write
# =============================================================================


def shared_parameter_file_for_writes(doc):
    """Path of the shared parameter file for new definitions.

    The file that Revit is set to use, then the fallback of the Parameter
    Manager. If there is none, a file in the temporary folder.
    """
    path, _ = registry_service.resolve_shared_parameter_file(doc)
    if path:
        return path
    path = os.path.join(tempfile.gettempdir(), "PDSO Shared Parameters.txt")
    if not os.path.isfile(path):
        with open(path, "w"):
            pass  # Revit fills an empty file
    return path


def _apply(doc, desired, removed, unbind_removed=False):
    """Run the Parameter Manager apply step. Raise PDSOError if it fails."""
    report = registry_service.PDSOParameterSetupService(doc).apply(
        desired,
        removed,
        shared_parameter_file_for_writes(doc),
        unbind_removed=unbind_removed,
    )
    errors = [text for level, text in report.messages if level == "error"]
    if errors or not report.success:
        saved = ""
        if report.tracked:
            # The model changed even so: say what was kept
            saved = " The tracked list was saved with {} parameters.".format(
                len(report.tracked)
            )
        raise PDSOError(
            "The parameters were not all set up: {}{}".format(
                " ".join(errors) or "unknown error", saved
            ),
            409,
        )
    return [text for level, text in report.messages if level == "warning"]


def _require_configured(doc):
    registry = registry_service.get_registry(doc, refresh=True)
    if not registry.is_configured:
        raise PDSOError(
            "The parameters of the model are not set up. Run the bootstrap first.",
            409,
        )
    return registry


PARAMETER_SETS = ("all", "required")


def setup_default(doc, which="all"):
    """Track the built-in parameters, if the model tracks no parameter yet.

    Args:
        which (str): "all": every built-in parameter (the "Default" preset of
            the Parameter Manager). "required": only the parameters the tools
            need (the "Blank" preset): Program UID, Program Stacking, Last
            Synced, and the parameters the area scheme mappings write into.

    A model that tracks parameters is not changed.

    Returns:
        dict: {"created": [names], "existing": [names]}
    """
    if which not in PARAMETER_SETS:
        raise PDSOError(
            "parameters must be one of: {}".format(", ".join(PARAMETER_SETS))
        )
    registry = registry_service.get_registry(doc, refresh=True)
    if registry.is_configured:
        return {"created": [], "existing": registry.names()}
    if which == "all":
        desired = list(standard_pdso_parameters.values())
    else:
        names = list(CORE_PARAMETER_NAMES)
        for name in registry_service.get_area_mapped_parameter_names(doc):
            if name in standard_pdso_parameters and name not in names:
                names.append(name)
        desired = [
            definition
            for name, definition in standard_pdso_parameters.items()
            if name in names
        ]
    _apply(doc, desired, [])
    return {
        "created": registry_service.get_registry(doc, refresh=True).names(),
        "existing": [],
    }


def _shared_parameters_named(doc, name):
    return [
        element
        for element in DB.FilteredElementCollector(doc).OfClass(
            DB.SharedParameterElement
        )
        if element.Name == name
    ]


def _type_of(shared_parameter_element):
    try:
        return parameter_type_from_spec(
            shared_parameter_element.GetDefinition().GetDataType()
        )
    except Exception:
        return None


def with_prefix(doc, name):
    """The Revit name for a new parameter: PREFIX is added, as in the Parameter Manager.

    "Tenant Type" gives "PDSO - Tenant Type". A name that starts with PREFIX is
    kept. So is a name the model has a shared parameter for already, so that
    parameter and its values are used again.
    """
    name = (name or "").strip()
    if not name or name.startswith(PREFIX) or name in standard_pdso_parameters:
        return name
    if _shared_parameters_named(doc, name):
        return name
    return PREFIX + name


def create_parameter(doc, name, parameter_type="string"):
    """Track a new parameter: a custom parameter, or a built-in one by its name.

    A custom name gets the PREFIX ("PDSO - "), like a parameter added in the
    Parameter Manager, unless the model has a shared parameter with the name as
    given (see with_prefix). The field name is the same either way.

    If the model has one shared parameter with the name already (for example
    after a delete without purge), that parameter is used, and its values stay.

    Args:
        doc: Revit Document (a project, with no open transaction)
        name (str): Parameter name, with or without PREFIX
        parameter_type (str): "string", "multilineText", "number", "integer",
            "area", "length" or "yesno". Not used for a built-in parameter.

    Returns:
        dict: The parameter (see list_parameters), "reattached" (True if the
            model had the shared parameter already) and "warnings"
    """
    registry = _require_configured(doc)
    name = with_prefix(doc, name)
    error = registry_service.validate_parameter_name(name)
    if error:
        raise PDSOError(error)
    if not slug(name):
        raise PDSOError("Give a parameter name with letters or digits.")
    if name in registry:
        raise PDSOError("The model tracks the parameter '{}'.".format(name), 409)
    field = field_name(name)
    used = by_field(doc)
    if field in used:
        raise PDSOError(
            "The field name '{}' is in use by the parameter '{}'. "
            "Use a different name.".format(field, used[field].name),
            409,
        )

    existing = _shared_parameters_named(doc, name)
    if name in standard_pdso_parameters:
        definition = standard_pdso_parameters[name]
    else:
        if parameter_type not in PARAMETER_TYPE_ORDER:
            raise PDSOError(
                "Parameter type '{}' is not permitted. Use one of: {}".format(
                    parameter_type, ", ".join(PARAMETER_TYPE_ORDER)
                )
            )
        if len(existing) == 1 and _type_of(existing[0]) != parameter_type:
            raise PDSOError(
                "The model has the shared parameter '{}' with the type '{}'. Use "
                "that type, or delete the parameter with purge.".format(
                    name, _type_of(existing[0])
                ),
                409,
            )
        definition = ExtendedSharedParameter(
            name, parameter_type, False, None, role=ROLE_CUSTOM
        )

    warnings = _apply(doc, list(registry.parameters) + [definition], [])
    created = [item for item in all_parameters(doc, True) if item.name == name]
    if not created:
        raise PDSOError("The parameter '{}' was not created.".format(name), 500)
    check = registry_service.check_parameter_bindings(doc)
    info = _info(created[0], check["problems"])
    info["reattached"] = len(existing) > 0
    info["warnings"] = warnings
    return info


def _find_tracked(doc, reference):
    text = str(reference).strip()
    for field in all_parameters(doc, True):
        if text in (field.field, field.name):
            return field
    return None


def delete_parameter(doc, reference, purge=False):
    """Stop tracking a parameter.

    Args:
        doc: Revit Document (a project, with no open transaction)
        reference (str): Field name or parameter name
        purge (bool): False: PDSO stops tracking the parameter. The Revit
            parameter and its values stay, and create_parameter with the same
            name tracks it again. True: also unbind the parameter from Generic
            Models. This deletes its values on all elements.

    Returns:
        dict: {"name", "detached" (it was tracked and is not tracked now),
            "purged" (it was unbound from Generic Models), "warnings"}
    """
    registry = _require_configured(doc)
    locked = set(CORE_PARAMETER_NAMES) | set(
        registry_service.get_area_mapped_parameter_names(doc)
    )
    target = _find_tracked(doc, reference)

    if target is not None:
        if target.name in locked:
            raise PDSOError(
                "'{}' is a required parameter (the PDSO tools use it, or an Area "
                "Scheme mapping writes into it) and cannot be deleted.".format(
                    target.name
                ),
                409,
            )
        desired = [
            parameter
            for parameter in registry.parameters
            if parameter.parameter_name != target.name
        ]
        warnings = _apply(doc, desired, [target.definition], unbind_removed=purge)
        return {
            "name": target.name,
            "detached": True,
            "purged": bool(purge),
            "warnings": warnings,
        }

    # A parameter that PDSO does not track is not changed here, with or without
    # purge: it can be a parameter of the model that PDSO did not make.
    raise PDSOError(
        "PDSO does not track a parameter '{}'. To delete the values of a "
        "parameter that was deleted without purge, create it again, then delete "
        "it with purge.".format(reference),
        404,
    )
