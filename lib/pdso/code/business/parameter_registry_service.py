# -*- coding: utf-8 -*-
"""PDSO Parameter Registry - which parameters a model tracks, and setting them up.

The tracked parameter list is stored in the document config
(PdsoConfigDocumentSchema.tethered_parameters, name -> shared parameter GUID).
It is written only by the Parameter Manager (PDSOParameterSetupService.apply).

Everything that needs "the PDSO parameters" (sync, preflight, audit) reads them
through get_registry() instead of a hard-coded list.

A model that has never run the Parameter Manager has an empty registry: it is
"not configured", and the preflight blocks the sync until the manager is run.
"""

# Python standard libraries
import os
import traceback

# 3rd Party Modules
from pyrevit import script
from rpw import DB

# Custom Modules
from common.code.background import TimedOut, call_with_timeout
from pdso.code.business.extensible_storage_service import PDSOExtensibleStorageService
from pdso.code.data.extended_shared_parameter import (
    ExtendedSharedParameter,
    ROLE_AREA,
    ROLE_CUSTOM,
    parameter_type_from_spec,
    parameter_type_label,
    spec_type_id_for,
)
from pdso.code.data.shared_parameter_constants import (
    CORE_PARAMETER_NAMES,
    SHARED_PARAMETER_GROUP_NAME,
    standard_pdso_parameters,
)

logger = script.get_logger()

# Characters Revit does not allow in parameter names
INVALID_NAME_CHARACTERS = '\\:{}[]|;<>?`~'

# pyRevit user config section used to remember a fallback shared parameter file
_USER_CONFIG_SECTION = "PDSO_ParameterManager"
_USER_CONFIG_OPTION = "fallback_shared_parameter_file"


# =============================================================================
# Small helpers
# =============================================================================


def _net_dict_to_python(value):
    """Convert a .NET IDictionary (or None) into a plain Python dict."""
    if not value:
        return {}
    if hasattr(value, "Keys"):
        return dict((k, value[k]) for k in value.Keys)
    return dict(value)


def _generic_model_category(doc):
    return doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_GenericModel)


def _category_set(doc, categories):
    category_set = doc.Application.Create.NewCategorySet()
    for category in categories:
        category_set.Insert(category)
    return category_set


def _binding_has_category(binding, category):
    for bound_category in binding.Categories:
        if bound_category.Id == category.Id:
            return True
    return False


def _shared_parameter_elements_by_name(doc):
    """Return {name: [SharedParameterElement, ...]} for every shared parameter in the model."""
    by_name = {}
    collector = DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement)
    for spe in collector:
        by_name.setdefault(spe.Name, []).append(spe)
    return by_name


def _bound_non_shared_parameter_names(doc):
    """Names of project parameters that are bound in the model but are NOT shared."""
    names = set()
    iterator = doc.ParameterBindings.ForwardIterator()
    iterator.Reset()
    while iterator.MoveNext():
        definition = iterator.Key
        try:
            element = doc.GetElement(definition.Id)
        except Exception:
            element = None
        if element is not None and not isinstance(element, DB.SharedParameterElement):
            names.add(definition.Name)
    return names


def validate_parameter_name(name):
    """Return an error message for an invalid parameter name, or None if it is valid."""
    if not name or not name.strip():
        return "Parameter name can't be empty"
    if name != name.strip():
        return "Parameter name can't start or end with a space"
    bad = [c for c in INVALID_NAME_CHARACTERS if c in name]
    if bad:
        return "Parameter name can't contain: {}".format(" ".join(bad))
    return None


def get_area_mapped_parameter_names(doc):
    """Names of parameters that Area Scheme mappings write Area values into."""
    storage_service = PDSOExtensibleStorageService(doc)
    config = storage_service.get_document_config()
    if config is None:
        return []
    # Key: name of the parameter that gets the Area value. Value: name of the
    # Area Scheme. (See PDSOSyncService: ExtendedAreaScheme(doc, value, key).)
    mappings = _net_dict_to_python(config.area_scheme_mappings)
    return [k for k in mappings.keys() if k]


# =============================================================================
# Registry (read side)
# =============================================================================


class TrackedParameterRegistry(object):
    """Snapshot of the parameters a model tracks.

    Attributes:
        parameters (list[ExtendedSharedParameter]): Tracked parameters, in display order
        is_configured (bool): False if the Parameter Manager has never been run
    """

    def __init__(self, parameters, is_configured):
        self.parameters = parameters
        self.is_configured = is_configured
        self._by_name = dict((p.parameter_name, p) for p in parameters)

    def names(self):
        return [p.parameter_name for p in self.parameters]

    def get(self, name):
        return self._by_name.get(name)

    def __contains__(self, name):
        return name in self._by_name

    def by_role(self, *roles):
        return [p for p in self.parameters if p.role in roles]

    def missing_core_parameter_names(self):
        return [n for n in CORE_PARAMETER_NAMES if n not in self._by_name]


def load_registry(doc):
    """Read the tracked parameters from the document config.

    Args:
        doc: Revit Document

    Returns:
        TrackedParameterRegistry
    """
    storage_service = PDSOExtensibleStorageService(doc)
    config = storage_service.get_document_config()
    if config is None:
        return TrackedParameterRegistry([], False)

    tethered = _net_dict_to_python(config.tethered_parameters)
    # Mapping key: parameter name. Mapping value: Area Scheme name.
    area_mapped = set(
        k for k in _net_dict_to_python(config.area_scheme_mappings).keys() if k
    )

    parameters = []
    custom = []
    for name, guid in tethered.items():
        catalog_entry = standard_pdso_parameters.get(name)
        if catalog_entry is not None:
            # The GUID may differ from the catalog's if an existing parameter was adopted
            parameters.append(catalog_entry.copy(guid_string=guid))
            continue

        parameter_type = None
        spe = DB.SharedParameterElement.Lookup(doc, guid)
        if spe is not None:
            try:
                parameter_type = parameter_type_from_spec(
                    spe.GetDefinition().GetDataType()
                )
            except Exception:
                parameter_type = None

        custom.append(
            ExtendedSharedParameter(
                name,
                parameter_type,
                False,
                guid,
                role=ROLE_AREA if name in area_mapped else ROLE_CUSTOM,
            )
        )

    # Built-ins in catalog order, then custom parameters alphabetically
    catalog_order = list(standard_pdso_parameters.keys())
    parameters.sort(key=lambda p: catalog_order.index(p.parameter_name))
    custom.sort(key=lambda p: p.parameter_name.lower())

    return TrackedParameterRegistry(parameters + custom, len(tethered) > 0)


_registry_cache = {}


def _doc_key(doc):
    return "{}|{}".format(doc.Title, doc.PathName)


def get_registry(doc, refresh=False):
    """Return the tracked parameter registry for a document, cached.

    The pyRevit engine for these tools is persistent, so the cache lives across
    button clicks. Top-level operations (preflight, sync, audit) pass
    refresh=True; per-element code can rely on the cached copy.
    """
    key = _doc_key(doc)
    if refresh or key not in _registry_cache:
        _registry_cache[key] = load_registry(doc)
    return _registry_cache[key]


def invalidate_registry(doc=None):
    """Drop cached registries (all of them, or one document's)."""
    if doc is None:
        _registry_cache.clear()
    else:
        _registry_cache.pop(_doc_key(doc), None)


def check_parameter_bindings(doc, registry=None):
    """Check that every tracked parameter is bound to Generic Models as an instance parameter.

    Args:
        doc: Revit Document
        registry: TrackedParameterRegistry (loaded fresh if None)

    Returns:
        dict with keys:
            configured (bool): False if the Parameter Manager has never been run
            found (list[str]): Tracked parameters correctly bound
            missing (list[str]): Tracked parameters missing or bound incorrectly
            problems (dict[str, str]): Reason per missing parameter
            missing_core (list[str]): Required parameters that aren't tracked at all
                (core parameters, and parameters an Area Scheme mapping writes into)
    """
    if registry is None:
        registry = get_registry(doc, refresh=True)

    missing_required = registry.missing_core_parameter_names()
    for name in get_area_mapped_parameter_names(doc):
        if name not in registry and name not in missing_required:
            missing_required.append(name)

    result = {
        "configured": registry.is_configured,
        "found": [],
        "missing": [],
        "problems": {},
        "missing_core": missing_required,
    }
    if not registry.is_configured:
        return result

    generic_model = _generic_model_category(doc)
    for parameter in registry.parameters:
        name = parameter.parameter_name
        spe = DB.SharedParameterElement.Lookup(doc, parameter.parameter_guid)
        problem = None
        if spe is None:
            problem = "not in the model"
        else:
            binding = doc.ParameterBindings.get_Item(spe.GetDefinition())
            if binding is None:
                problem = "in the model but not bound to any category"
            elif not isinstance(binding, DB.InstanceBinding):
                problem = "bound as a type parameter (must be instance)"
            elif not _binding_has_category(binding, generic_model):
                problem = "not bound to Generic Models"

        if problem:
            result["missing"].append(name)
            result["problems"][name] = problem
        else:
            result["found"].append(name)

    return result


# =============================================================================
# Shared parameter file
# =============================================================================


# A shared parameter file on a disconnected network drive can make a plain
# os.path.isfile() hang for several seconds (Windows retries the connection).
# Every existence check goes through path_exists() with a time limit instead.
DEFAULT_PATH_TIMEOUT_S = 3.0


def path_exists(path, timeout=DEFAULT_PATH_TIMEOUT_S):
    """os.path.isfile() with a time limit.

    Args:
        path (str): File path to check
        timeout (float): Seconds to wait before giving up

    Returns:
        bool or None: True/False, or None if the check timed out (e.g. an
        unreachable network drive)
    """
    if not path:
        return False
    try:
        return call_with_timeout(os.path.isfile, timeout, path)
    except TimedOut:
        return None
    except Exception:
        return False


def get_configured_shared_parameter_path(doc):
    """Path of the shared parameter file Revit is set to use (no file access)."""
    return doc.Application.SharedParametersFilename or ""


def get_configured_shared_parameter_file(doc, timeout=DEFAULT_PATH_TIMEOUT_S):
    """Return (path, exists) for the shared parameter file Revit is set to use.

    exists is None if the check timed out.
    """
    path = get_configured_shared_parameter_path(doc)
    return path, path_exists(path, timeout)


def get_remembered_fallback_path():
    """The fallback file path this user picked earlier (no file access)."""
    try:
        config = script.get_config(_USER_CONFIG_SECTION)
        return config.get_option(_USER_CONFIG_OPTION, "") or ""
    except Exception:
        return ""


def get_fallback_shared_parameter_file(timeout=DEFAULT_PATH_TIMEOUT_S):
    """Return the fallback file this user picked earlier, if it still exists."""
    path = get_remembered_fallback_path()
    return path if path_exists(path, timeout) else ""


def remember_fallback_shared_parameter_file(path):
    """Remember a fallback shared parameter file for this user (pyRevit config)."""
    try:
        config = script.get_config(_USER_CONFIG_SECTION)
        config.set_option(_USER_CONFIG_OPTION, path)
        script.save_config()
    except Exception as e:
        logger.warning("Could not remember shared parameter file: {}".format(e))


def resolve_shared_parameter_file_path(
    configured_path, fallback_path, timeout=DEFAULT_PATH_TIMEOUT_S
):
    """Pick the shared parameter file to use. Pure file access; safe off the Revit thread.

    Order: the file Revit is configured to use, then the fallback this user
    picked earlier.

    Args:
        configured_path (str): Revit's SharedParametersFilename
        fallback_path (str): The remembered fallback path
        timeout (float): Seconds to wait on each existence check

    Returns:
        tuple(str, str, bool or None): (path or "", source, configured_exists)
            source is "revit", "fallback" or "".
            configured_exists is None if checking Revit's file timed out.
    """
    configured_exists = path_exists(configured_path, timeout)
    if configured_exists:
        return configured_path, "revit", True
    if path_exists(fallback_path, timeout):
        return fallback_path, "fallback", configured_exists
    return "", "", configured_exists


def resolve_shared_parameter_file(doc, timeout=DEFAULT_PATH_TIMEOUT_S):
    """Pick the shared parameter file to use, without asking the user.

    Returns:
        tuple(str, str): (path or "", source) where source is "revit", "fallback" or ""
    """
    path, source, _ = resolve_shared_parameter_file_path(
        get_configured_shared_parameter_path(doc),
        get_remembered_fallback_path(),
        timeout,
    )
    return path, source


# =============================================================================
# Apply (write side)
# =============================================================================


class ApplyReport(object):
    """Outcome of PDSOParameterSetupService.apply()."""

    def __init__(self):
        self.messages = []  # list of (level, text); level is "ok", "info", "warning" or "error"
        self.tracked = {}  # name -> Guid saved to the config
        self.success = False

    def ok(self, text):
        self.messages.append(("ok", text))

    def info(self, text):
        self.messages.append(("info", text))

    def warning(self, text):
        self.messages.append(("warning", text))
        logger.warning(text)

    def error(self, text):
        self.messages.append(("error", text))
        logger.error(text)

    def count(self, level):
        return len([m for m in self.messages if m[0] == level])


class _Resolved(object):
    """Working state for one parameter during apply()."""

    def __init__(self, parameter):
        self.parameter = parameter
        self.guid = parameter.parameter_guid  # None for a brand new custom parameter
        self.model_definition = None  # InternalDefinition if already in the model
        self.external_definition = None  # ExternalDefinition from the shared parameter file
        self.failed = False


class PDSOParameterSetupService(object):
    """Creates shared parameters, binds them to Generic Models, and saves the tracked list.

    Must run in a valid Revit API context (e.g. an ExternalEvent) with no open transaction.
    """

    def __init__(self, doc):
        self.doc = doc
        self.app = doc.Application
        self.logger = script.get_logger()

    def apply(
        self,
        desired_parameters,
        removed_parameters,
        shared_parameter_file,
        unbind_removed=False,
        data_silo_family_name=None,
    ):
        """Make the model track exactly `desired_parameters`.

        Args:
            desired_parameters (list[ExtendedSharedParameter]): Parameters to track.
                A brand new custom parameter has parameter_guid None.
            removed_parameters (list[ExtendedSharedParameter]): Previously tracked
                parameters the user stopped tracking.
            shared_parameter_file (str): Path of the shared parameter file to use
            unbind_removed (bool): Also unbind removed parameters from Generic Models
                (and remove them from the Data Silo family). Deletes their values.
            data_silo_family_name (str): If given, also add the tracked parameters
                to this family as instance parameters.

        Returns:
            ApplyReport
        """
        report = ApplyReport()
        original_file = self.app.SharedParametersFilename

        try:
            # 1. Shared parameter file definitions (file writes can't be undone)
            resolved = self._resolve_definitions(
                desired_parameters, shared_parameter_file, report
            )
            if resolved is None:
                return report

            # 2. Bind to Generic Models in the project
            if not self._bind_parameters(resolved, removed_parameters, unbind_removed, report):
                return report

            # 3. Data Silo family
            if data_silo_family_name:
                self._update_data_silo_family(
                    data_silo_family_name,
                    resolved,
                    removed_parameters if unbind_removed else [],
                    report,
                )

            # 4. Save the tracked list. A parameter that was tracked before and
            # failed this time stays tracked: dropping it would silently stop
            # the sync from writing it.
            storage_service = PDSOExtensibleStorageService(self.doc)
            previous = {}
            config = storage_service.get_document_config()
            if config is not None:
                previous = _net_dict_to_python(config.tethered_parameters)
            tracked = {}
            for item in resolved:
                name = item.parameter.parameter_name
                if not item.failed and item.guid is not None:
                    tracked[name] = item.guid
                elif name in previous:
                    tracked[name] = previous[name]
                    report.warning(
                        "'{}' could not be set up this time; it stays tracked as "
                        "before.".format(name)
                    )

            if not tracked:
                report.error("No parameters could be set up; nothing was saved.")
                return report

            storage_service.ensure_schemas_exist()
            if not storage_service.update_document_config(tethered_parameters=tracked):
                report.error(
                    "Parameters were bound, but the tracked list couldn't be saved to the "
                    "PDSO configuration. If the model is workshared, check that nobody "
                    "else has Project Information checked out."
                )
                return report

            report.tracked = tracked
            report.success = report.count("error") == 0
            report.ok("Saved {} tracked parameters to the PDSO configuration.".format(len(tracked)))
            return report

        except Exception as e:
            report.error("Unexpected error: {}".format(e))
            self.logger.error(traceback.format_exc())
            return report

        finally:
            try:
                self.app.SharedParametersFilename = original_file
            except Exception:
                pass
            invalidate_registry(self.doc)

    def add_tracked_parameters_to_family(self, family_name, shared_parameter_file):
        """Add the model's tracked parameters to the Data Silo family.

        For a Data Silo family loaded after the Parameter Manager ran (Quick Setup
        loads it last). The project binding already puts every tracked parameter
        on Data Silos; this also embeds them in the family. Bindings and the
        tracked list aren't changed, and no parameter is removed from the family.

        Args:
            family_name (str): Name of the loaded Data Silo family
            shared_parameter_file (str): Shared parameter file to read definitions
                from (a missing definition is added to it, with the model's GUID)

        Returns:
            ApplyReport: success is True if nothing failed. A model that tracks
                no parameters yet gives an info message and success True.
        """
        report = ApplyReport()
        registry = get_registry(self.doc, refresh=True)
        if not registry.is_configured or not registry.parameters:
            report.info(
                "The model tracks no parameters yet. Run the Parameter Manager; it "
                "adds them to the Data Silo family."
            )
            report.success = True
            return report

        original_file = self.app.SharedParametersFilename
        try:
            resolved = self._resolve_definitions(
                list(registry.parameters), shared_parameter_file, report
            )
            if resolved is None:
                return report
            self._update_data_silo_family(family_name, resolved, [], report)
            report.success = report.count("error") == 0
            return report
        except Exception as e:
            report.error("Unexpected error: {}".format(e))
            self.logger.error(traceback.format_exc())
            return report
        finally:
            try:
                self.app.SharedParametersFilename = original_file
            except Exception:
                pass

    # -------------------------------------------------------------------------

    def _resolve_definitions(self, desired_parameters, shared_parameter_file, report):
        """Find or create a definition for every desired parameter.

        For each parameter, in order:
        1. The model already has a shared parameter with this GUID: use it.
        2. The model has exactly one shared parameter with this name: adopt its GUID.
        3. The model has a non-shared project parameter with this name: error.
        Then, for binding (if not in the model) and for the family:
        4. The file has a definition with this GUID: use it.
        5. The file has a definition with this name: use it (warn if the GUID differs).
        6. Otherwise create it in the file's "PDSO" group.
        """
        exists = path_exists(shared_parameter_file)
        if not exists:
            report.error(
                "Shared parameter file {}: '{}'".format(
                    "couldn't be reached" if exists is None else "not found",
                    shared_parameter_file,
                )
            )
            return None

        self.app.SharedParametersFilename = shared_parameter_file
        definition_file = self.app.OpenSharedParameterFile()
        if definition_file is None:
            report.error(
                "Revit couldn't read '{}' as a shared parameter file.".format(
                    shared_parameter_file
                )
            )
            return None
        report.info("Using shared parameter file: {}".format(shared_parameter_file))

        # Index what's already in the file
        file_by_guid = {}
        file_by_name = {}
        for group in definition_file.Groups:
            for definition in group.Definitions:
                file_by_guid[definition.GUID] = definition
                file_by_name.setdefault(definition.Name, []).append(definition)

        pdso_group = definition_file.Groups.get_Item(SHARED_PARAMETER_GROUP_NAME)

        model_by_name = _shared_parameter_elements_by_name(self.doc)
        non_shared_names = _bound_non_shared_parameter_names(self.doc)

        resolved = []
        for parameter in desired_parameters:
            item = _Resolved(parameter)
            resolved.append(item)
            name = parameter.parameter_name
            wanted_spec = spec_type_id_for(parameter.parameter_type) if parameter.parameter_type else None

            # --- Model side -------------------------------------------------
            spe = None
            if item.guid is not None:
                spe = DB.SharedParameterElement.Lookup(self.doc, item.guid)
                if spe is not None and spe.Name != name:
                    report.error(
                        "'{}': its GUID is already used in this model by a parameter named "
                        "'{}'. Skipped.".format(name, spe.Name)
                    )
                    item.failed = True
                    continue

            if spe is None and name in model_by_name:
                candidates = model_by_name[name]
                if len(candidates) > 1:
                    report.error(
                        "'{}': the model has {} shared parameters with this name. Delete "
                        "the duplicates in Manage > Project Parameters, then run again. "
                        "Skipped.".format(name, len(candidates))
                    )
                    item.failed = True
                    continue
                spe = candidates[0]
                if item.guid is not None and spe.GuidValue != item.guid:
                    report.warning(
                        "'{}': the model already has this parameter with a different GUID "
                        "({}) than the PDSO default. Using the model's parameter.".format(
                            name, spe.GuidValue
                        )
                    )
                item.guid = spe.GuidValue

            if spe is None and name in non_shared_names:
                report.error(
                    "'{}': a project parameter with this name exists but isn't a shared "
                    "parameter. Rename or delete it, then run again. Skipped.".format(name)
                )
                item.failed = True
                continue

            if spe is not None:
                item.model_definition = spe.GetDefinition()
                model_spec = item.model_definition.GetDataType()
                if wanted_spec is not None and model_spec != wanted_spec:
                    report.error(
                        "'{}': already in the model as type '{}', not '{}'. A shared "
                        "parameter's type can't be changed. Skipped.".format(
                            name,
                            parameter_type_label(parameter_type_from_spec(model_spec)) or model_spec.TypeId,
                            parameter_type_label(parameter.parameter_type),
                        )
                    )
                    item.failed = True
                    continue

            # --- File side --------------------------------------------------
            definition = file_by_guid.get(item.guid) if item.guid is not None else None
            if definition is not None and definition.Name != name:
                report.warning(
                    "'{}': the shared parameter file has this GUID under the name '{}'.".format(
                        name, definition.Name
                    )
                )
                if item.model_definition is None:
                    report.error("'{}': skipped to avoid a name mismatch.".format(name))
                    item.failed = True
                    continue
                definition = None

            if definition is None and name in file_by_name:
                same_name = file_by_name[name]
                if item.model_definition is None:
                    definition = same_name[0]
                    if len(same_name) > 1:
                        report.warning(
                            "'{}': the shared parameter file has {} definitions with this "
                            "name. Using the one in group '{}'.".format(
                                name, len(same_name), definition.OwnerGroup.Name
                            )
                        )
                    if item.guid is not None and definition.GUID != item.guid:
                        report.warning(
                            "'{}': the shared parameter file's GUID ({}) differs from the "
                            "PDSO default. Using the file's definition, so schedules and "
                            "tags from other PDSO projects won't match this one.".format(
                                name, definition.GUID
                            )
                        )
                    item.guid = definition.GUID
                # else: the model's parameter wins. The file's same-name definition has a
                # different GUID, so it can't be used for the family. Handled below.

            if definition is not None and wanted_spec is not None:
                if definition.GetDataType() != wanted_spec:
                    report.error(
                        "'{}': the shared parameter file defines it as '{}', not '{}'. "
                        "Skipped.".format(
                            name,
                            parameter_type_label(parameter_type_from_spec(definition.GetDataType())),
                            parameter_type_label(parameter.parameter_type),
                        )
                    )
                    item.failed = True
                    continue

            if definition is None:
                # Create it in the PDSO group, unless a same-name definition is already there
                if pdso_group is None:
                    pdso_group = definition_file.Groups.Create(SHARED_PARAMETER_GROUP_NAME)
                if pdso_group.Definitions.get_Item(name) is not None:
                    if item.model_definition is None:
                        report.error(
                            "'{}': couldn't create it in the shared parameter file. "
                            "Skipped.".format(name)
                        )
                        item.failed = True
                        continue
                    report.warning(
                        "'{}': is in the model, but the shared parameter file has a "
                        "different parameter with this name, so it can't be added to the "
                        "Data Silo family.".format(name)
                    )
                else:
                    spec = wanted_spec
                    if spec is None and item.model_definition is not None:
                        spec = item.model_definition.GetDataType()
                    options = DB.ExternalDefinitionCreationOptions(name, spec)
                    if item.guid is not None:
                        options.GUID = item.guid
                    options.UserModifiable = parameter.user_modifiable
                    if parameter.description:
                        options.Description = parameter.description
                    definition = pdso_group.Definitions.Create(options)
                    item.guid = definition.GUID
                    report.ok(
                        "'{}': added to the shared parameter file (group '{}').".format(
                            name, SHARED_PARAMETER_GROUP_NAME
                        )
                    )

            item.external_definition = definition

        return resolved

    def _bind_parameters(self, resolved, removed_parameters, unbind_removed, report):
        """Bind tracked parameters to Generic Models; optionally unbind removed ones."""
        doc = self.doc
        generic_model = _generic_model_category(doc)
        transaction = DB.Transaction(doc, "PDSO - Set Up Tracked Parameters")
        transaction.Start()
        try:
            for item in resolved:
                if item.failed:
                    continue
                name = item.parameter.parameter_name
                definition = item.model_definition or item.external_definition
                binding = (
                    doc.ParameterBindings.get_Item(item.model_definition)
                    if item.model_definition is not None
                    else None
                )

                if binding is None:
                    new_binding = doc.Application.Create.NewInstanceBinding(
                        _category_set(doc, [generic_model])
                    )
                    if doc.ParameterBindings.Insert(definition, new_binding, DB.GroupTypeId.Data):
                        report.ok("'{}': bound to Generic Models.".format(name))
                    else:
                        report.error("'{}': Revit refused to bind it.".format(name))
                        item.failed = True
                elif not isinstance(binding, DB.InstanceBinding):
                    report.error(
                        "'{}': is a type parameter in this model. PDSO needs an instance "
                        "parameter. Change it in Manage > Project Parameters. Skipped.".format(name)
                    )
                    item.failed = True
                elif not _binding_has_category(binding, generic_model):
                    categories = list(binding.Categories) + [generic_model]
                    new_binding = doc.Application.Create.NewInstanceBinding(
                        _category_set(doc, categories)
                    )
                    if doc.ParameterBindings.ReInsert(definition, new_binding, DB.GroupTypeId.Data):
                        report.ok("'{}': added Generic Models to its categories.".format(name))
                    else:
                        report.error("'{}': Revit refused to rebind it.".format(name))
                        item.failed = True
                else:
                    report.info("'{}': already set up.".format(name))

            for parameter in removed_parameters:
                name = parameter.parameter_name
                if not unbind_removed:
                    report.info("'{}': no longer tracked (left in the model).".format(name))
                    continue
                spe = DB.SharedParameterElement.Lookup(doc, parameter.parameter_guid)
                if spe is None:
                    report.info("'{}': no longer tracked (wasn't in the model).".format(name))
                    continue
                definition = spe.GetDefinition()
                binding = doc.ParameterBindings.get_Item(definition)
                if binding is None or not _binding_has_category(binding, generic_model):
                    report.info("'{}': no longer tracked (wasn't on Generic Models).".format(name))
                    continue
                remaining = [c for c in binding.Categories if c.Id != generic_model.Id]
                if remaining:
                    new_binding = (
                        doc.Application.Create.NewInstanceBinding(_category_set(doc, remaining))
                        if isinstance(binding, DB.InstanceBinding)
                        else doc.Application.Create.NewTypeBinding(_category_set(doc, remaining))
                    )
                    doc.ParameterBindings.ReInsert(definition, new_binding, DB.GroupTypeId.Data)
                    report.warning(
                        "'{}': removed from Generic Models (kept on its other categories). "
                        "Its Generic Model values are deleted.".format(name)
                    )
                else:
                    doc.ParameterBindings.Remove(definition)
                    report.warning(
                        "'{}': unbound from the model. Its values are deleted.".format(name)
                    )

            transaction.Commit()
            return True

        except Exception as e:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            report.error(
                "Binding parameters failed and was rolled back: {}. If the model is "
                "workshared, make sure you can edit project parameters (nobody else has "
                "them checked out).".format(e)
            )
            self.logger.error(traceback.format_exc())
            return False

    def _update_data_silo_family(self, family_name, resolved, removed_parameters, report):
        """Add tracked parameters to the Data Silo family (and remove untracked ones if asked)."""
        doc = self.doc
        family = None
        for candidate in DB.FilteredElementCollector(doc).OfClass(DB.Family):
            if candidate.Name == family_name:
                family = candidate
                break

        if family is None:
            report.warning(
                "Data Silo family '{}' isn't loaded, so it wasn't updated. The parameters "
                "are still bound to Generic Models in the project.".format(family_name)
            )
            return
        if family.IsInPlace or not family.IsEditable:
            report.warning("Data Silo family '{}' can't be edited; skipped.".format(family_name))
            return

        family_doc = None
        try:
            family_doc = doc.EditFamily(family)
            manager = family_doc.FamilyManager
            changed = False

            transaction = DB.Transaction(family_doc, "PDSO - Data Silo Parameters")
            transaction.Start()
            try:
                for item in resolved:
                    if item.failed or item.external_definition is None:
                        continue
                    name = item.parameter.parameter_name
                    if manager.get_Parameter(item.guid) is not None:
                        continue
                    existing = manager.get_Parameter(name)
                    if existing is not None:
                        report.warning(
                            "'{}': the Data Silo family already has a different parameter "
                            "with this name; not added to the family.".format(name)
                        )
                        continue
                    manager.AddParameter(item.external_definition, DB.GroupTypeId.Data, True)
                    changed = True
                    report.ok("'{}': added to the Data Silo family.".format(name))

                for parameter in removed_parameters:
                    family_parameter = manager.get_Parameter(parameter.parameter_guid)
                    if family_parameter is not None:
                        manager.RemoveParameter(family_parameter)
                        changed = True
                        report.warning(
                            "'{}': removed from the Data Silo family.".format(
                                parameter.parameter_name
                            )
                        )
                transaction.Commit()
            except Exception:
                if transaction.HasStarted() and not transaction.HasEnded():
                    transaction.RollBack()
                raise

            if changed:
                # Imported here to avoid a circular import at module load
                from pdso.code.business.family_manager_service import (
                    ProgramVolumeFamilyLoadOptions,
                )

                # Overwrite the family but keep instance parameter values in the project
                family_doc.LoadFamily(doc, ProgramVolumeFamilyLoadOptions(True, False))
                report.ok("Reloaded Data Silo family '{}'.".format(family_name))
            else:
                report.info("Data Silo family '{}' already up to date.".format(family_name))

        except Exception as e:
            report.error(
                "Couldn't update the Data Silo family '{}': {}. The parameters are still "
                "bound to Generic Models in the project.".format(family_name, e)
            )
            self.logger.error(traceback.format_exc())
        finally:
            if family_doc is not None:
                try:
                    family_doc.Close(False)
                except Exception:
                    pass
