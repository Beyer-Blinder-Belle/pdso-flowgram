# -*- coding: utf-8 -*-
"""ViewModel for the PDSO Parameter Manager.

Lets users choose which parameters the PDSO tools track:
- Built-in parameters (from the catalog) are listed as options; core ones are locked.
- Custom parameters can be added, and removed again before they are created.
- Presets (Default, Blank, or user-saved) tick a set of parameters in one go,
  and the current selection can be saved as a new preset.

"Apply" creates/reuses shared parameter definitions, binds them to Generic Models,
optionally embeds them in the Data Silo family, and saves the tracked list to the
document config. Nothing is created until Apply runs at least once.
"""

# Python standard libraries
import os
import threading
import traceback
from collections import OrderedDict

# 3rd Party Modules
from pyrevit import revit, DB, forms

# System/Windows Modules
from System.Windows.Controls import (
    Button,
    CheckBox,
    ColumnDefinition,
    ComboBox,
    ComboBoxItem,
    Grid,
    Image,
    TextBlock,
    TextBox,
)
from System.Windows import (
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    TextWrapping,
    Thickness,
    VerticalAlignment,
)
import System
from System import Action, Guid

# Custom Modules
from base_view_model import PDSO_Base_View_Model
from common.code import generic_utils
from common.code.background import TimedOut, call_with_timeout
from common.code.view_model_external_events import CustomizableEvent
from pdso.code.business.extensible_storage_service import PDSOExtensibleStorageService
from pdso.code.business.parameter_preset_service import (
    DEFAULT_PRESET_NAME,
    ParameterPreset,
    PresetParameter,
    find_existing_preset_file,
    get_builtin_presets,
    get_preset_folder,
    list_user_presets,
    save_preset,
    set_preset_folder,
    validate_preset_name,
)
from pdso.code.business.parameter_registry_service import (
    PDSOParameterSetupService,
    check_parameter_bindings,
    get_area_mapped_parameter_names,
    get_configured_shared_parameter_path,
    get_registry,
    get_remembered_fallback_path,
    remember_fallback_shared_parameter_file,
    resolve_shared_parameter_file_path,
    validate_parameter_name,
)
from pdso.code.data.extended_shared_parameter import (
    ExtendedSharedParameter,
    PARAMETER_TYPE_ORDER,
    ROLE_AREA,
    ROLE_CORE,
    ROLE_CUSTOM,
    parameter_type_label,
)
from pdso.code.data.shared_parameter_constants import standard_pdso_parameters

COLOR_OK = "#4CAF50"
COLOR_WARNING = "#FF9800"
COLOR_ERROR = "#F44336"
COLOR_MUTED = "#666666"

# Dropdown entry that shows what the model tracks now (not a preset)
CURRENT_SETUP_LABEL = "Current model setup"

# Seconds to wait on the presets folder (it may be on a network drive)
PRESET_FOLDER_TIMEOUT_S = 10.0


class ParameterRow(object):
    """One row in the Parameter Manager (plain state; rows are rebuilt on change)."""

    def __init__(
        self,
        name,
        parameter_type,
        role,
        guid,
        is_builtin,
        is_tracked,
        was_tracked,
        is_locked=False,
        lock_reason="",
        from_preset=False,
    ):
        self.name = name
        self.parameter_type = parameter_type
        self.role = role
        self.guid = guid  # None for a new custom parameter
        self.is_builtin = is_builtin
        self.is_tracked = is_tracked
        self.was_tracked = was_tracked  # tracked in the saved config
        self.is_locked = is_locked
        self.lock_reason = lock_reason
        self.from_preset = from_preset  # custom row loaded from a preset
        self.status_text = ""
        self.status_color = COLOR_MUTED

    @property
    def is_new(self):
        """A custom row that isn't in the saved config yet (can be removed)."""
        return not self.is_builtin and not self.was_tracked

    @property
    def is_editable(self):
        """Name and type can be edited: a new custom row typed in by the user.

        Rows from a preset carry a GUID, so their name and type stay fixed.
        """
        return self.is_new and not self.is_locked and self.guid is None

    def to_definition(self):
        """Build the ExtendedSharedParameter this row stands for."""
        if self.is_builtin:
            return standard_pdso_parameters[self.name].copy(guid_string=self.guid)
        return ExtendedSharedParameter(
            self.name.strip(),
            self.parameter_type,
            False,
            self.guid,
            role=self.role,
            # Area values are written by the sync; keep users from editing them
            user_modifiable=self.role != ROLE_AREA,
            description="Written by PDSO Area Harvest. Do not edit."
            if self.role == ROLE_AREA
            else "",
        )


class PDSO_View_Model_3_Parameters(PDSO_Base_View_Model):
    """ViewModel for the Parameter Manager window."""

    def __init__(self):
        super(PDSO_View_Model_3_Parameters, self).__init__()

        self.rows = []
        self._is_initializing = False
        self._has_unsaved_changes = False
        self._is_configured = False

        self._setup_status_message = ""
        self._setup_status_color = COLOR_MUTED
        self._shared_parameter_file_message = ""
        self._shared_parameter_file_color = COLOR_MUTED
        self._shared_parameter_file_path = ""
        self._shared_parameter_file_configured = ""
        self._shared_parameter_file_check_done = False

        self.data_silo_family_name = ""
        self.add_to_family = True
        self.unbind_removed = False

        self.built_in_container = None
        self.custom_container = None

        # Presets
        self._builtin_presets = get_builtin_presets()
        self._user_presets = []
        self._preset_folder = get_preset_folder()
        self._preset_folder_message = ""
        self._selected_preset_name = DEFAULT_PRESET_NAME
        self._rows_edited = False  # hand edits since the last preset was loaded
        self._suppress_preset_selection = False
        self._preset_load_id = 0
        self.preset_combo = None

        # Cached model state the rows are built from (filled by _load_state)
        self._registry = None
        self._binding_check = None
        self._area_mapped = set()
        self._model_names = set()

        self.custom_event_handler = CustomizableEvent()

    # =========================================================================
    # Setup
    # =========================================================================

    def setup(self):
        """Complete setup of the view model (called by pyRevit after window creation)."""
        super(PDSO_View_Model_3_Parameters, self).setup("3_parameters.xaml")
        self._is_initializing = True
        try:
            PDSOExtensibleStorageService(revit.doc).ensure_schemas_exist()
            self._load_state()
            self._wire_controls()
            self._refresh_rows_ui()
            self._populate_preset_combo()
            self._start_preset_load()
        except Exception as e:
            self.logger.error("Error during Parameter Manager setup: {}".format(e))
            self.logger.error(traceback.format_exc())
        finally:
            self._is_initializing = False

    def _load_state(self):
        """Read config, registry and the model to build the rows (needs API context)."""
        doc = revit.doc

        storage_service = PDSOExtensibleStorageService(doc)
        config = storage_service.get_document_config()
        self.data_silo_family_name = (config.data_silo_family_name or "") if config else ""
        self.on_property_changed("add_to_family_label")

        registry = get_registry(doc, refresh=True)
        self._is_configured = registry.is_configured
        binding_check = check_parameter_bindings(doc, registry)

        # Cache what the rows are built from, so presets can rebuild them later
        # without calling the Revit API from the UI thread
        self._registry = registry
        self._binding_check = binding_check
        self._area_mapped = set(get_area_mapped_parameter_names(doc))
        self._model_names = set(
            spe.Name
            for spe in DB.FilteredElementCollector(doc).OfClass(DB.SharedParameterElement)
        )

        # A model that's set up shows its own setup; a new one starts from Default
        if registry.is_configured:
            self._selected_preset_name = CURRENT_SETUP_LABEL
            self._build_rows(None)
        else:
            self._selected_preset_name = DEFAULT_PRESET_NAME
            self._build_rows(self._find_preset(DEFAULT_PRESET_NAME))
        self._rows_edited = False

        # Overall status
        if not registry.is_configured:
            self.setup_status_message = (
                "Parameters haven't been set up for this model yet. Review the list, then "
                "click Apply Parameters. The sync is blocked until this has run once."
            )
            self.setup_status_color = COLOR_ERROR
        elif binding_check["missing"] or binding_check["missing_core"]:
            self.setup_status_message = (
                "{} tracked parameter(s) are missing or set up incorrectly. Click Apply "
                "Parameters to fix them.".format(
                    len(binding_check["missing"]) + len(binding_check["missing_core"])
                )
            )
            self.setup_status_color = COLOR_ERROR
        else:
            self.setup_status_message = "{} parameters tracked and set up.".format(
                len(registry.parameters)
            )
            self.setup_status_color = COLOR_OK

        # Shared parameter file: read the path now, check it in the background.
        # The file is often on a network drive; if that drive is unreachable,
        # checking it can take many seconds, and the window shouldn't wait for it.
        self._shared_parameter_file_configured = get_configured_shared_parameter_path(doc)
        self._start_shared_parameter_file_check()

        self.has_unsaved_changes = not registry.is_configured

    # =========================================================================
    # Rows
    # =========================================================================

    def _build_rows(self, preset):
        """Build the rows from the cached model state, optionally ticked from a preset.

        Args:
            preset (ParameterPreset or None): None shows the model's current setup.
        """
        registry = self._registry
        area_mapped = self._area_mapped
        rows = []

        # Built-in parameters
        preset_builtins = preset.builtin_names if preset is not None else None
        for name, catalog_entry in standard_pdso_parameters.items():
            tracked_entry = registry.get(name)
            if preset is not None:
                is_tracked = name in preset_builtins
            else:
                is_tracked = tracked_entry is not None
            row = ParameterRow(
                name,
                catalog_entry.parameter_type,
                catalog_entry.role,
                tracked_entry.parameter_guid if tracked_entry else catalog_entry.parameter_guid,
                is_builtin=True,
                is_tracked=is_tracked,
                was_tracked=tracked_entry is not None,
            )
            if catalog_entry.role == ROLE_CORE:
                row.is_locked, row.lock_reason = True, "Required by the PDSO tools"
            elif name in area_mapped:
                row.is_locked, row.lock_reason = True, "Used by an Area Scheme mapping"
            if row.is_locked:
                row.is_tracked = True
            rows.append(row)

        # Custom parameters the model already tracks
        preset_customs = OrderedDict()
        if preset is not None:
            for entry in preset.custom_parameters:
                preset_customs[entry.name] = entry
        for parameter in registry.parameters:
            name = parameter.parameter_name
            if name in standard_pdso_parameters:
                continue
            in_preset = preset_customs.pop(name, None) is not None
            row = ParameterRow(
                name,
                parameter.parameter_type,
                parameter.role,
                parameter.parameter_guid,
                is_builtin=False,
                is_tracked=preset is None or in_preset,
                was_tracked=True,
            )
            if name in area_mapped:
                row.is_locked, row.lock_reason = True, "Used by an Area Scheme mapping"
                row.is_tracked = True
            rows.append(row)

        # Custom parameters from the preset that the model doesn't track yet
        for entry in preset_customs.values():
            guid = None
            if entry.guid:
                try:
                    guid = Guid(entry.guid)
                except Exception:
                    self.logger.warning(
                        "Preset parameter '{}' has an invalid GUID; a new one will be "
                        "created.".format(entry.name)
                    )
            row = ParameterRow(
                entry.name,
                entry.parameter_type,
                ROLE_AREA if entry.name in area_mapped else ROLE_CUSTOM,
                guid,
                is_builtin=False,
                is_tracked=True,
                was_tracked=False,
                from_preset=True,
            )
            if entry.name in area_mapped:
                row.is_locked, row.lock_reason = True, "Used by an Area Scheme mapping"
            rows.append(row)

        # Area Scheme mappings that write into a parameter nobody has set up yet
        known = set(r.name for r in rows)
        for name in sorted(area_mapped - known):
            rows.append(
                ParameterRow(
                    name,
                    "area",
                    ROLE_AREA,
                    None,
                    is_builtin=False,
                    is_tracked=True,
                    was_tracked=False,
                    is_locked=True,
                    lock_reason="Used by an Area Scheme mapping",
                )
            )

        for row in rows:
            self._set_row_status(row)
        self.rows = rows

    def _set_row_status(self, row):
        """Set a row's status text from the cached model state."""
        binding_check = self._binding_check
        if row.was_tracked and row.is_tracked:
            if row.name in binding_check["found"]:
                row.status_text, row.status_color = "In model", COLOR_OK
            else:
                row.status_text = binding_check["problems"].get(row.name, "Missing")
                row.status_color = COLOR_ERROR
        elif row.is_tracked:
            if row.name in self._model_names:
                row.status_text, row.status_color = "In model - will be tracked", COLOR_WARNING
            else:
                row.status_text, row.status_color = "Will be created", COLOR_WARNING
        elif row.was_tracked:
            row.status_text, row.status_color = "Will stop being tracked", COLOR_WARNING
        else:
            if row.name in self._model_names:
                row.status_text = "In model - not tracked"
            else:
                row.status_text = "Not tracked"
            row.status_color = COLOR_MUTED

    # =========================================================================
    # Presets
    # =========================================================================

    def _all_presets(self):
        return self._builtin_presets + self._user_presets

    def _find_preset(self, name):
        for preset in self._all_presets():
            if preset.name.lower() == (name or "").lower():
                return preset
        return None

    @property
    def preset_folder_message(self):
        return self._preset_folder_message

    @preset_folder_message.setter
    def preset_folder_message(self, value):
        self._preset_folder_message = value
        self.on_property_changed("preset_folder_message")

    def _populate_preset_combo(self):
        """Fill the preset dropdown and select the current preset (no events fired)."""
        if self.preset_combo is None:
            return
        self._suppress_preset_selection = True
        try:
            self.preset_combo.Items.Clear()
            entries = []
            if self._is_configured:
                entries.append((CURRENT_SETUP_LABEL, CURRENT_SETUP_LABEL))
            for preset in self._builtin_presets:
                label = "{} (built-in)".format(preset.name)
                entries.append((label, preset.name))
            for preset in self._user_presets:
                entries.append((preset.name, preset.name))

            for label, key in entries:
                item = ComboBoxItem()
                item.Content = label
                item.Tag = key
                self.preset_combo.Items.Add(item)
                if key.lower() == (self._selected_preset_name or "").lower():
                    self.preset_combo.SelectedItem = item
        finally:
            self._suppress_preset_selection = False

    def _start_preset_load(self):
        """Read user presets from the presets folder off the UI thread."""
        folder = self._preset_folder
        self._preset_load_id += 1
        load_id = self._preset_load_id
        self.preset_folder_message = "Presets folder: {} (loading...)".format(folder)

        def load():
            try:
                presets, problems = call_with_timeout(
                    list_user_presets, PRESET_FOLDER_TIMEOUT_S, folder
                )
                outcome = (presets, problems, None)
            except TimedOut:
                outcome = ([], [], "couldn't be reached (network drive offline?)")
            except Exception as e:
                outcome = ([], [], "couldn't be read: {}".format(e))
            try:
                self.Dispatcher.BeginInvoke(
                    Action(lambda: self._on_presets_loaded(load_id, folder, *outcome))
                )
            except Exception:
                pass  # window was closed

        worker = threading.Thread(target=load)
        worker.daemon = True
        worker.start()

    def _on_presets_loaded(self, load_id, folder, presets, problems, error):
        """Show the presets read in the background (runs on the UI thread)."""
        if load_id != self._preset_load_id:
            return  # a newer load has started since
        self._user_presets = presets
        if error:
            self.preset_folder_message = "Presets folder: {} {}".format(folder, error)
        else:
            self.preset_folder_message = "Presets folder: {} ({} saved preset{})".format(
                folder, len(presets), "" if len(presets) == 1 else "s"
            )
        for problem in problems:
            self.logger.warning("Skipped preset file {}".format(problem))
        self._populate_preset_combo()

    def _on_preset_selected(self, sender, args):
        if self._suppress_preset_selection or self._registry is None:
            return
        selected = sender.SelectedItem
        if selected is None:
            return
        key = selected.Tag
        if key == self._selected_preset_name:
            return

        if self._rows_edited:
            if not forms.alert(
                "Load '{}'? This replaces the ticks and custom rows you've changed "
                "here.".format(key),
                title="PDSO Parameter Manager",
                sub_msg="Nothing has been applied to the model yet, so nothing is lost "
                "from the model.",
                yes=True,
                no=True,
            ):
                self._populate_preset_combo()  # put the old selection back
                return

        preset = None if key == CURRENT_SETUP_LABEL else self._find_preset(key)
        self._selected_preset_name = key
        self._build_rows(preset)
        self._refresh_rows_ui()
        self._rows_edited = False
        # Loading a preset is a change to apply, unless it's the model's own setup
        self.has_unsaved_changes = preset is not None or not self._is_configured

    def _on_save_preset_clicked(self, sender, args):
        try:
            errors = self._validate_rows()
            if errors:
                forms.alert(
                    "Fix these before saving a preset:",
                    title="PDSO Parameter Manager",
                    expanded="\n".join(errors),
                )
                return

            current = self._find_preset(self._selected_preset_name)
            default_name = (
                current.name if current is not None and not current.is_builtin else ""
            )
            name = forms.ask_for_string(
                default=default_name,
                prompt="Name for this preset (e.g. 'Residential' or 'Hospitality'):",
                title="Save Parameter Preset",
            )
            if name is None:
                return
            name = name.strip()
            problem = validate_preset_name(name)
            if problem:
                forms.alert(problem, title="Save Parameter Preset")
                return

            parameters = []
            no_guid = []
            for row in self.rows:
                if not row.is_tracked:
                    continue
                if row.is_builtin:
                    parameters.append(PresetParameter(row.name, True))
                else:
                    if row.guid is None:
                        no_guid.append(row.name.strip())
                    parameters.append(
                        PresetParameter(
                            row.name.strip(),
                            False,
                            row.parameter_type,
                            str(row.guid) if row.guid is not None else None,
                        )
                    )

            if no_guid and not forms.alert(
                "Save without fixed GUIDs for: {}?".format(", ".join(no_guid)),
                title="Save Parameter Preset",
                sub_msg="These custom parameters haven't been created yet, so they "
                "have no GUID. Each project that loads this preset will create its "
                "own, and schedules or tags won't match across projects. To avoid "
                "this, click Apply Parameters first, then save the preset.",
                yes=True,
                no=True,
            ):
                return

            folder = self._preset_folder
            existing_path = call_with_timeout(
                find_existing_preset_file, PRESET_FOLDER_TIMEOUT_S, folder, name
            )
            if existing_path and not forms.alert(
                "A preset named '{}' already exists. Replace it?".format(name),
                title="Save Parameter Preset",
                yes=True,
                no=True,
            ):
                return

            preset = ParameterPreset(name, parameters)
            path = call_with_timeout(
                save_preset, PRESET_FOLDER_TIMEOUT_S, folder, preset, existing_path
            )
            self.output.print_md("Saved parameter preset **{}** to `{}`".format(name, path))

            # Show it as the selected preset; the ticks already match it
            self._selected_preset_name = name
            self._rows_edited = False
            self._start_preset_load()

        except TimedOut:
            forms.alert(
                "The presets folder couldn't be reached:\n{}".format(self._preset_folder),
                title="Save Parameter Preset",
                sub_msg="Check the network connection, or use Change Folder to pick "
                "another folder.",
            )
        except Exception as e:
            self.logger.error("Error saving preset: {}".format(e))
            self.logger.error(traceback.format_exc())
            forms.alert(
                "The preset couldn't be saved: {}".format(e), title="Save Parameter Preset"
            )

    def _on_change_preset_folder_clicked(self, sender, args):
        folder = forms.pick_folder(title="Choose a folder for PDSO parameter presets")
        if not folder:
            return
        set_preset_folder(folder)
        self._preset_folder = folder
        self._start_preset_load()

    # =========================================================================
    # Shared parameter file check (background)
    # =========================================================================

    def _start_shared_parameter_file_check(self):
        """Check the shared parameter file off the UI thread, then update the status."""
        configured_path = self._shared_parameter_file_configured
        fallback_path = get_remembered_fallback_path()
        self._shared_parameter_file_path = ""
        self._shared_parameter_file_check_done = False
        # Each check gets an id so a slow, older check can't overwrite a newer result
        self._shared_parameter_file_check_id = getattr(
            self, "_shared_parameter_file_check_id", 0
        ) + 1
        check_id = self._shared_parameter_file_check_id
        self.shared_parameter_file_message = "Checking {}...".format(
            configured_path or "for a shared parameter file"
        )
        self.shared_parameter_file_color = COLOR_MUTED

        def check():
            # File access only: no Revit API calls on this thread
            try:
                outcome = resolve_shared_parameter_file_path(
                    configured_path, fallback_path, timeout=15.0
                )
            except Exception:
                outcome = ("", "", False)
            try:
                self.Dispatcher.BeginInvoke(
                    Action(
                        lambda: self._on_shared_parameter_file_checked(
                            check_id, *outcome
                        )
                    )
                )
            except Exception:
                pass  # window was closed

        worker = threading.Thread(target=check)
        worker.daemon = True
        worker.start()

    def _on_shared_parameter_file_checked(
        self, check_id, path, source, configured_exists
    ):
        """Show the result of the background check (runs on the UI thread)."""
        if check_id != self._shared_parameter_file_check_id:
            return  # a newer check has started since
        configured_path = self._shared_parameter_file_configured
        self._shared_parameter_file_path = path
        self._shared_parameter_file_check_done = True

        if source == "revit":
            self.shared_parameter_file_message = path
            self.shared_parameter_file_color = COLOR_OK
            return

        if configured_path:
            problem = (
                "couldn't be reached (network drive offline?)"
                if configured_exists is None
                else "can't be found"
            )
            configured_text = "Revit's file '{}' {}".format(configured_path, problem)
        else:
            configured_text = "No shared parameter file is set in Revit"

        if source == "fallback":
            self.shared_parameter_file_message = (
                "{}. Using the file you picked earlier: {}".format(configured_text, path)
            )
            self.shared_parameter_file_color = COLOR_WARNING
        else:
            self.shared_parameter_file_message = (
                "{}. You'll be asked to pick or create one when you apply.".format(
                    configured_text
                )
            )
            self.shared_parameter_file_color = COLOR_ERROR

    # =========================================================================
    # Observable properties
    # =========================================================================

    @property
    def setup_status_message(self):
        return self._setup_status_message

    @setup_status_message.setter
    def setup_status_message(self, value):
        self._setup_status_message = value
        self.on_property_changed("setup_status_message")

    @property
    def setup_status_color(self):
        return self._setup_status_color

    @setup_status_color.setter
    def setup_status_color(self, value):
        self._setup_status_color = value
        self.on_property_changed("setup_status_color")

    @property
    def shared_parameter_file_message(self):
        return self._shared_parameter_file_message

    @shared_parameter_file_message.setter
    def shared_parameter_file_message(self, value):
        self._shared_parameter_file_message = value
        self.on_property_changed("shared_parameter_file_message")

    @property
    def shared_parameter_file_color(self):
        return self._shared_parameter_file_color

    @shared_parameter_file_color.setter
    def shared_parameter_file_color(self, value):
        self._shared_parameter_file_color = value
        self.on_property_changed("shared_parameter_file_color")

    @property
    def add_to_family_label(self):
        return "Also add tracked parameters to the Data Silo family ('{}')".format(
            self.data_silo_family_name or "not configured"
        )

    @property
    def has_unsaved_changes(self):
        return self._has_unsaved_changes

    @has_unsaved_changes.setter
    def has_unsaved_changes(self, value):
        if self._has_unsaved_changes != value:
            self._has_unsaved_changes = value
            self.on_property_changed("has_unsaved_changes")
            self.on_property_changed("unsaved_changes_message")
            self.on_property_changed("unsaved_changes_color")

    @property
    def unsaved_changes_message(self):
        if not self._has_unsaved_changes:
            return "Model matches the list above"
        if not self._is_configured:
            return "Not applied yet"
        return "Changes not applied; close this window to discard them"

    @property
    def unsaved_changes_color(self):
        return COLOR_WARNING if self._has_unsaved_changes else COLOR_OK

    def _mark_changed(self, manual=False):
        """Flag unsaved changes. manual=True for edits the user made by hand
        (used to warn before a preset replaces them)."""
        if not self._is_initializing:
            self.has_unsaved_changes = True
            if manual:
                self._rows_edited = True

    def _on_row_toggled(self, row, status_block):
        self._set_row_status(row)
        status_block.Text = row.status_text
        status_block.Foreground = System.Windows.Media.BrushConverter().ConvertFromString(
            row.status_color
        )
        self._mark_changed(manual=True)

    # =========================================================================
    # Controls
    # =========================================================================

    def _wire_controls(self):
        presenter = self.FindName("BodyContent")
        if not presenter or not presenter.Content:
            self.logger.warning("BodyContent not loaded")
            return
        content = presenter.Content

        self.built_in_container = self.find_element_by_name(
            content, "BuiltInParametersContainer"
        )
        self.custom_container = self.find_element_by_name(
            content, "CustomParametersContainer"
        )

        add_button = self.find_element_by_name(content, "AddCustomParameterButton")
        if add_button:
            add_button.Click += self._on_add_custom_clicked

        self.preset_combo = self.find_element_by_name(content, "PresetComboBox")
        if self.preset_combo:
            self.preset_combo.SelectionChanged += self._on_preset_selected

        save_preset_button = self.find_element_by_name(content, "SavePresetButton")
        if save_preset_button:
            save_preset_button.Click += self._on_save_preset_clicked

        change_folder_button = self.find_element_by_name(content, "ChangePresetFolderButton")
        if change_folder_button:
            change_folder_button.Click += self._on_change_preset_folder_clicked

        apply_button = self.find_element_by_name(content, "ApplyButton")
        if apply_button:
            apply_button.Click += self._on_apply_clicked

        family_checkbox = self.find_element_by_name(content, "AddToFamilyCheckBox")
        if family_checkbox:
            family_checkbox.IsChecked = self.add_to_family
            family_checkbox.Checked += lambda s, a: self._set_option("add_to_family", True)
            family_checkbox.Unchecked += lambda s, a: self._set_option("add_to_family", False)

        unbind_checkbox = self.find_element_by_name(content, "UnbindRemovedCheckBox")
        if unbind_checkbox:
            unbind_checkbox.IsChecked = self.unbind_removed
            unbind_checkbox.Checked += lambda s, a: self._set_option("unbind_removed", True)
            unbind_checkbox.Unchecked += lambda s, a: self._set_option("unbind_removed", False)

    def _set_option(self, name, value):
        setattr(self, name, value)
        self._mark_changed()

    def _on_add_custom_clicked(self, sender, args):
        self.rows.append(
            ParameterRow(
                "PDSO - ",
                "string",
                ROLE_CUSTOM,
                None,
                is_builtin=False,
                is_tracked=True,
                was_tracked=False,
            )
        )
        self.rows[-1].status_text = "Will be created"
        self.rows[-1].status_color = COLOR_WARNING
        self._refresh_rows_ui()
        self._mark_changed(manual=True)

    # =========================================================================
    # Row UI
    # =========================================================================

    def _refresh_rows_ui(self):
        if self.built_in_container is not None:
            self.built_in_container.Children.Clear()
            self.built_in_container.Children.Add(self._create_header())
            for row in self.rows:
                if row.is_builtin:
                    self.built_in_container.Children.Add(self._create_row(row))

        if self.custom_container is not None:
            self.custom_container.Children.Clear()
            custom_rows = [r for r in self.rows if not r.is_builtin]
            if not custom_rows:
                placeholder = TextBlock()
                placeholder.Text = "No custom parameters. Click '+ Add Custom Parameter' to add one."
                placeholder.Foreground = System.Windows.Media.Brushes.Gray
                placeholder.FontStyle = System.Windows.FontStyles.Italic
                placeholder.Margin = Thickness(5, 5, 5, 5)
                self.custom_container.Children.Add(placeholder)
            else:
                self.custom_container.Children.Add(self._create_header())
                for row in custom_rows:
                    self.custom_container.Children.Add(self._create_row(row))

    def _new_grid(self):
        grid = Grid()
        grid.Margin = Thickness(0, 2, 0, 2)
        for width, unit in [
            (28, GridUnitType.Pixel),
            (5, GridUnitType.Star),
            (2, GridUnitType.Star),
            (3, GridUnitType.Star),
            (26, GridUnitType.Pixel),
        ]:
            column = ColumnDefinition()
            column.Width = GridLength(width, unit)
            grid.ColumnDefinitions.Add(column)
        return grid

    def _create_header(self):
        grid = self._new_grid()
        for column, title in [(0, "Track"), (1, "Parameter"), (2, "Type"), (3, "Status")]:
            header = TextBlock()
            header.Text = title
            header.FontWeight = System.Windows.FontWeights.Bold
            header.FontSize = 10 if column == 0 else 12
            header.Margin = Thickness(2, 0, 6, 4)
            Grid.SetColumn(header, column)
            grid.Children.Add(header)
        return grid

    def _create_row(self, row):
        grid = self._new_grid()

        # Track checkbox
        checkbox = CheckBox()
        checkbox.IsChecked = row.is_tracked
        checkbox.IsEnabled = not row.is_locked
        checkbox.VerticalAlignment = VerticalAlignment.Center
        checkbox.Margin = Thickness(4, 0, 0, 0)
        if row.is_locked:
            checkbox.ToolTip = row.lock_reason

        def on_checked(sender, args):
            row.is_tracked = True
            self._on_row_toggled(row, status)

        def on_unchecked(sender, args):
            row.is_tracked = False
            self._on_row_toggled(row, status)

        checkbox.Checked += on_checked
        checkbox.Unchecked += on_unchecked
        Grid.SetColumn(checkbox, 0)
        grid.Children.Add(checkbox)

        # Name
        if row.is_editable:
            name_box = TextBox()
            name_box.Text = row.name
            name_box.Margin = Thickness(2, 0, 6, 0)
            name_box.VerticalAlignment = VerticalAlignment.Center

            def on_name_changed(sender, args):
                row.name = sender.Text
                self._mark_changed(manual=True)

            name_box.TextChanged += on_name_changed
            name_control = name_box
        else:
            name_control = TextBlock()
            name_control.Text = row.name
            name_control.TextWrapping = TextWrapping.Wrap
            name_control.Margin = Thickness(2, 0, 6, 0)
            name_control.VerticalAlignment = VerticalAlignment.Center
            if row.is_locked:
                name_control.ToolTip = row.lock_reason
                name_control.FontWeight = System.Windows.FontWeights.SemiBold
        Grid.SetColumn(name_control, 1)
        grid.Children.Add(name_control)

        # Type
        if row.is_editable:
            type_combo = ComboBox()
            type_combo.Margin = Thickness(0, 0, 6, 0)
            type_combo.VerticalAlignment = VerticalAlignment.Center
            for parameter_type in PARAMETER_TYPE_ORDER:
                item = ComboBoxItem()
                item.Content = parameter_type_label(parameter_type)
                item.Tag = parameter_type
                type_combo.Items.Add(item)
                if parameter_type == row.parameter_type:
                    type_combo.SelectedItem = item

            def on_type_changed(sender, args):
                selected = sender.SelectedItem
                if selected is not None:
                    row.parameter_type = selected.Tag
                    self._mark_changed(manual=True)

            type_combo.SelectionChanged += on_type_changed
            type_control = type_combo
        else:
            type_control = TextBlock()
            type_control.Text = parameter_type_label(row.parameter_type)
            type_control.Margin = Thickness(2, 0, 6, 0)
            type_control.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(type_control, 2)
        grid.Children.Add(type_control)

        # Status
        status = TextBlock()
        status.Text = row.status_text
        status.TextWrapping = TextWrapping.Wrap
        status.FontSize = 10
        status.Margin = Thickness(2, 0, 4, 0)
        status.VerticalAlignment = VerticalAlignment.Center
        status.Foreground = System.Windows.Media.BrushConverter().ConvertFromString(
            row.status_color
        )
        Grid.SetColumn(status, 3)
        grid.Children.Add(status)

        # Remove (only for custom rows that haven't been created yet)
        if row.is_new and not row.is_locked:
            remove_button = Button()
            remove_button.Width = 20
            remove_button.Height = 20
            remove_button.Padding = Thickness(1)
            remove_button.VerticalAlignment = VerticalAlignment.Center
            remove_button.HorizontalAlignment = HorizontalAlignment.Center
            remove_button.ToolTip = "Remove this row"
            icon_path = os.path.join(generic_utils.lib_path(), "common", "assets", "x_icon.png")
            if os.path.exists(icon_path):
                button_image = Image()
                button_image.Source = generic_utils.load_image_source(icon_path)
                remove_button.Content = button_image
            else:
                remove_button.Content = "x"

            def on_remove(sender, args):
                self.rows.remove(row)
                self._refresh_rows_ui()
                self._mark_changed(manual=True)

            remove_button.Click += on_remove
            Grid.SetColumn(remove_button, 4)
            grid.Children.Add(remove_button)

        return grid

    # =========================================================================
    # Apply
    # =========================================================================

    def _validate_rows(self):
        """Return a list of validation errors (empty if the rows are valid)."""
        errors = []
        seen = {}
        builtin_names = set(n.lower() for n in standard_pdso_parameters.keys())
        for row in self.rows:
            if not row.is_tracked:
                continue
            name = row.name.strip() if row.is_new else row.name
            if row.is_new:
                problem = validate_parameter_name(row.name)
                if problem:
                    errors.append("'{}': {}".format(row.name, problem))
                    continue
                if name == "PDSO -" or name == "PDSO - ":
                    errors.append("A custom parameter still has the placeholder name.")
                    continue
                if name.lower() in builtin_names:
                    errors.append(
                        "'{}' is a built-in parameter; tick it in the Built-in list "
                        "instead.".format(name)
                    )
                    continue
                if not row.parameter_type:
                    errors.append("'{}': choose a type.".format(name))
            key = name.lower()
            if key in seen:
                errors.append("'{}' is listed more than once.".format(name))
            seen[key] = True
        return errors

    def _choose_shared_parameter_file(self):
        """Return the shared parameter file to use, asking the user if needed."""
        if self._shared_parameter_file_check_done:
            if self._shared_parameter_file_path:
                return self._shared_parameter_file_path
        else:
            # Background check hasn't finished: check now, but don't wait forever
            path, _, _ = resolve_shared_parameter_file_path(
                self._shared_parameter_file_configured,
                get_remembered_fallback_path(),
                timeout=5.0,
            )
            if path:
                return path

        configured = self._shared_parameter_file_configured
        message = (
            "Revit's shared parameter file can't be found:\n{}".format(configured)
            if configured
            else "No shared parameter file is set in Revit."
        )
        choice = forms.alert(
            message,
            title="PDSO Parameter Manager",
            sub_msg="The PDSO parameters must be written to a shared parameter file. "
            "Revit's setting won't be changed; your choice is remembered for the "
            "PDSO tools only.",
            options=["Select an existing file", "Create a new file"],
        )
        path = None
        if choice == "Select an existing file":
            path = forms.pick_file(file_ext="txt", title="Select a shared parameter file")
        elif choice == "Create a new file":
            path = forms.save_file(
                file_ext="txt",
                default_name="PDSO_SharedParameters",
                title="Create a shared parameter file",
            )
            if path and not os.path.exists(path):
                open(path, "w").close()
        if not path:
            return None

        remember_fallback_shared_parameter_file(path)
        self._shared_parameter_file_path = path
        self._shared_parameter_file_check_done = True
        return path

    def _on_apply_clicked(self, sender, args):
        try:
            errors = self._validate_rows()
            if errors:
                forms.alert(
                    "Fix these before applying:",
                    title="PDSO Parameter Manager",
                    expanded="\n".join(errors),
                )
                return

            desired = [r.to_definition() for r in self.rows if r.is_tracked]
            removed = [r.to_definition() for r in self.rows if r.was_tracked and not r.is_tracked]
            family_name = self.data_silo_family_name if self.add_to_family else None

            shared_parameter_file = self._choose_shared_parameter_file()
            if not shared_parameter_file:
                return

            # Confirm
            summary = [
                "Track {} parameters (bound to Generic Models).".format(len(desired)),
                "Shared parameter file: {}".format(shared_parameter_file),
            ]
            if family_name:
                summary.append("Add them to the Data Silo family '{}' and reload it.".format(family_name))
            if removed:
                if self.unbind_removed:
                    summary.append(
                        "DELETE from the model (all values lost): {}".format(
                            ", ".join(r.parameter_name for r in removed)
                        )
                    )
                else:
                    summary.append(
                        "Stop tracking (left in the model): {}".format(
                            ", ".join(r.parameter_name for r in removed)
                        )
                    )
            summary.append(
                "New definitions are written to the shared parameter file straight away; "
                "Undo in Revit won't remove them from the file."
            )
            if not forms.alert(
                "Apply these parameter changes?",
                title="PDSO Parameter Manager",
                expanded="\n\n".join(summary),
                yes=True,
                no=True,
                warn_icon=bool(removed and self.unbind_removed),
            ):
                return

            self.custom_event_handler.raise_event(
                self._do_apply, self, desired, removed, shared_parameter_file, family_name
            )
        except Exception as e:
            self.logger.error("Error starting apply: {}".format(e))
            self.logger.error(traceback.format_exc())

    @staticmethod
    def _do_apply(view_model, desired, removed, shared_parameter_file, family_name):
        """Run apply in Revit API context (called by the external event)."""
        view_model._apply(desired, removed, shared_parameter_file, family_name)

    def _apply(self, desired, removed, shared_parameter_file, family_name):
        service = PDSOParameterSetupService(revit.doc)
        report = service.apply(
            desired,
            removed,
            shared_parameter_file,
            unbind_removed=self.unbind_removed,
            data_silo_family_name=family_name,
        )

        icons = {"ok": "✅", "info": "ℹ️", "warning": "⚠️", "error": "❌"}
        self.output.print_md("## PDSO Parameter Manager")
        for level, text in report.messages:
            self.output.print_md("{} {}".format(icons.get(level, ""), text))
        if report.success:
            self.output.print_md("**Done.** {} parameters tracked.".format(len(report.tracked)))
        else:
            self.output.print_md(
                "**Finished with {} error(s).** See the messages above.".format(
                    report.count("error")
                )
            )

        # Reload from the model so the rows show what actually happened
        # (_load_state also resets the unsaved-changes flag)
        self._is_initializing = True
        try:
            self._load_state()
            self._refresh_rows_ui()
            self._populate_preset_combo()
        finally:
            self._is_initializing = False
