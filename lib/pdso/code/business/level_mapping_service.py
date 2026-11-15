# -*- coding: utf-8 -*-
"""Service for parsing and mapping level stacking notation to Revit Level elements.

This service handles the three-stage level mapping process:
1. Parse stacking notation (e.g., "03-05, 10" or "A07-A09")
2. Map keys to Level elements via configuration
3. Validate and deduplicate results

Supports key-based range expansion for multi-building projects where keys
can include prefixes (e.g., "A07", "B03").
"""

from pyrevit import script

# Custom Modules
from pdso.code.view_models.config_mapping_item import ConfigMappingItem


# TO DO - Review this class and make sure its architecture is consistent with other services. Consider moving certain methods.
class ValidationResult(object):
    """Result object for level mapping validation.

    Contains validation status and detailed error information for each
    data silo that has parsing errors or missing mappings.

    Attributes:
        passed (bool): True if no errors or missing mappings found
        errors_by_data_silo_uid (dict): Maps UID string to list of parsing error messages
        missing_mappings_by_data_silo_uid (dict): Maps UID string to list of missing keys
    """

    def __init__(self):
        """Initialize empty validation result."""
        self.passed = True
        self.errors_by_data_silo_uid = {}
        self.missing_mappings_by_data_silo_uid = {}

    def add_parsing_error(self, data_silo_uid, error_message):
        """Add a parsing error for a specific data silo.

        Args:
            data_silo_uid (str): UID of the data silo
            error_message (str): Description of the parsing error
        """
        if data_silo_uid not in self.errors_by_data_silo_uid:
            self.errors_by_data_silo_uid[data_silo_uid] = []
        self.errors_by_data_silo_uid[data_silo_uid].append(error_message)
        self.passed = False

    def add_missing_mappings(self, data_silo_uid, missing_keys):
        """Add missing mappings for a specific data silo.

        Args:
            data_silo_uid (str): UID of the data silo
            missing_keys (list): List of keys that couldn't be mapped
        """
        if missing_keys:
            if data_silo_uid not in self.missing_mappings_by_data_silo_uid:
                self.missing_mappings_by_data_silo_uid[data_silo_uid] = []
            self.missing_mappings_by_data_silo_uid[data_silo_uid].extend(missing_keys)
            self.passed = False

    def has_errors_for_data_silo(self, data_silo_uid):
        """Check if a specific data silo has validation errors.

        Args:
            data_silo_uid (str): UID of the data silo to check

        Returns:
            bool: True if data silo has errors or missing mappings
        """
        has_parsing_errors = data_silo_uid in self.errors_by_data_silo_uid
        has_missing_mappings = data_silo_uid in self.missing_mappings_by_data_silo_uid
        return has_parsing_errors or has_missing_mappings

    def get_summary_message(self):
        """Generate formatted summary message for console output.

        Returns:
            str: Multi-line summary of all validation errors
        """
        if self.passed:
            return "Level mapping validation passed - no errors found."

        lines = ["Level Mapping Validation Failed:"]
        lines.append("")

        # Report parsing errors
        if self.errors_by_data_silo_uid:
            lines.append("Parsing Errors:")
            for uid, errors in sorted(self.errors_by_data_silo_uid.items()):
                lines.append("  Data Silo UID: {}".format(uid))
                for error in errors:
                    lines.append("    - {}".format(error))
            lines.append("")

        # Report missing mappings
        if self.missing_mappings_by_data_silo_uid:
            lines.append("Missing Level Mappings:")
            for uid, missing_keys in sorted(
                self.missing_mappings_by_data_silo_uid.items()
            ):
                lines.append("  Data Silo UID: {}".format(uid))
                lines.append("    Missing keys: {}".format(", ".join(missing_keys)))
            lines.append("")

        lines.append("Please fix these issues before syncing program volumes.")
        return "\n".join(lines)


class PDSOLevelMappingService(object):
    """Service for parsing stacking notation and mapping to Level elements.

    This service loads level mappings from extensible storage configuration
    and provides methods to parse stacking parameters, map keys to levels,
    and validate the complete mapping workflow.

    Attributes:
        doc: Revit document
        ext_storage_service: PDSOExtensibleStorageService for config access
        level_mappings_cache (dict): Maps key string to ElementId
        sorted_keys (list): Sorted list of mapping keys for range expansion
        logger: PyRevit logger
    """

    def __init__(self, doc, ext_storage_service):
        """Initialize service and load level mappings from storage.

        Args:
            doc: Revit document
            ext_storage_service: PDSOExtensibleStorageService instance
        """
        self.doc = doc
        self.ext_storage_service = ext_storage_service
        self.logger = script.get_logger()

        self.level_mappings_cache = {}
        self.sorted_keys = []

        self.load_level_mappings()

    def load_level_mappings(self):
        """Load level mappings from extensible storage into cache.

        Loads the levels_mapping dictionary from PdsoConfigDocumentSchema,
        converts from .NET Dictionary to Python dict, and builds a sorted
        key list for efficient range expansion.
        """
        config = self.ext_storage_service.get_document_config()
        if config:
            with config as c:
                level_mappings = c.levels_mapping or {}

                # Convert .NET Dictionary to Python dict
                if hasattr(level_mappings, "Keys"):
                    self.level_mappings_cache = {
                        k: level_mappings[k] for k in level_mappings.Keys
                    }
                else:
                    self.level_mappings_cache = level_mappings

        # Build sorted keys list for range expansion
        # Uses natural sort to handle "3" < "10" and compound keys like "B1-03"
        self.sorted_keys = sorted(
            self.level_mappings_cache.keys(), key=ConfigMappingItem.natural_sort_key
        )

        self.logger.debug(
            "Loaded {} level mappings".format(len(self.level_mappings_cache))
        )
        if self.sorted_keys:
            self.logger.debug("Sorted keys: {}".format(", ".join(self.sorted_keys)))
            self.logger.debug(
                "DEBUG: Complete sorted_keys list (count={}): {}".format(
                    len(self.sorted_keys), self.sorted_keys
                )
            )

    def parse_stacking_parameter(self, stacking_string, data_silo_uid):
        """Parse stacking notation into list of keys.

        Supports both individual keys and ranges:
        - Single keys: "03, 05, 10" returns ["03", "05", "10"]
        - Numeric ranges: "03-05" returns ["03", "04", "05"]
        - Key ranges: "A07-A09" returns ["A07", "A08", "A09"]

        Range expansion uses sorted key indices from level mapping config.
        If start or end key not found, returns error instead of partial range.

        Args:
            stacking_string (str): Stacking parameter value
            data_silo_uid (str): UID for error reporting

        Returns:
            tuple: (list of key strings, list of error messages)
        """
        if not stacking_string or not stacking_string.strip():
            return [], []

        keys = []
        errors = []

        # Split by comma to get segments
        segments = [s.strip() for s in stacking_string.split(",")]

        for segment in segments:
            if not segment:
                continue

            if "-" in segment:
                # Range notation (e.g., "03-05" or "A07-A09")
                parts = segment.split("-")
                if len(parts) == 2:
                    start_key = parts[0].strip()
                    end_key = parts[1].strip()

                    self.logger.debug(
                        "DEBUG: Parsing range segment '{}' with start_key='{}' end_key='{}'".format(
                            segment, start_key, end_key
                        )
                    )

                    # Try to expand range using sorted key indices
                    try:
                        start_idx = self.sorted_keys.index(start_key)
                        end_idx = self.sorted_keys.index(end_key)

                        self.logger.debug(
                            "DEBUG: Found indices start_idx={} end_idx={}".format(
                                start_idx, end_idx
                            )
                        )

                        if start_idx > end_idx:
                            errors.append(
                                "Invalid range '{}' - start key comes after end key".format(
                                    segment
                                )
                            )
                        else:
                            # Extract all keys between indices (inclusive)
                            range_keys = self.sorted_keys[start_idx : end_idx + 1]
                            self.logger.debug(
                                "DEBUG: Slice operation sorted_keys[{}:{}] produces: {}".format(
                                    start_idx, end_idx + 1, range_keys
                                )
                            )
                            keys.extend(range_keys)
                            self.logger.debug(
                                "Expanded range '{}' to: {}".format(
                                    segment, ", ".join(range_keys)
                                )
                            )
                    except ValueError:
                        # One or both keys not in sorted list
                        if (
                            start_key not in self.sorted_keys
                            and end_key not in self.sorted_keys
                        ):
                            errors.append(
                                "Range '{}' - both keys not found in level mappings".format(
                                    segment
                                )
                            )
                        elif start_key not in self.sorted_keys:
                            errors.append(
                                "Range '{}' - start key '{}' not found in level mappings".format(
                                    segment, start_key
                                )
                            )
                        else:
                            errors.append(
                                "Range '{}' - end key '{}' not found in level mappings".format(
                                    segment, end_key
                                )
                            )
                else:
                    errors.append(
                        "Invalid range format '{}' - expected 'start-end'".format(
                            segment
                        )
                    )
            else:
                # Single key
                keys.append(segment)

        # Remove duplicates while preserving order
        seen = set()
        unique_keys = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)

        return unique_keys, errors

    def map_keys_to_levels(self, keys, ext_level_collection):
        """Map list of keys to ExtendedLevel objects.

        Looks up each key in level_mappings_cache to get ElementId,
        then finds the matching ExtendedLevel in the collection.

        Args:
            keys (list): List of key strings to map
            ext_level_collection: ExtendedLevelCollection instance

        Returns:
            tuple: (list of ExtendedLevel objects, list of missing key strings)
        """
        mapped_levels = []
        missing_keys = []

        for key in keys:
            element_id = self.level_mappings_cache.get(key)

            self.logger.debug(
                "DEBUG: Mapping key '{}' - ElementId from cache: {}".format(
                    key, element_id.Value if element_id else "NOT FOUND"
                )
            )

            if element_id:
                # Find matching ExtendedLevel by ElementId
                ext_level = next(
                    (
                        el
                        for el in ext_level_collection.extended_levels
                        if el.rpw_level.Id.Value == element_id.Value
                    ),
                    None,
                )

                if ext_level:
                    mapped_levels.append(ext_level)
                    self.logger.debug(
                        "Mapped key '{}' to level '{}' (ElementId: {})".format(
                            key, ext_level.rpw_level.Name, element_id.Value
                        )
                    )
                else:
                    # Level was deleted after mapping was created
                    self.logger.warning(
                        "Level mapping for key '{}' references deleted level (ElementId: {})".format(
                            key, element_id.Value
                        )
                    )
                    missing_keys.append(key)
            else:
                missing_keys.append(key)

        return mapped_levels, missing_keys

    def enumerate_required_levels_for_data_silo(
        self, ext_data_silo, ext_level_collection
    ):
        """Enumerate all required levels for a data silo (canonical + stacking).

        Full workflow:
        1. Get canonical level (where data silo is placed)
        2. Parse stacking parameter
        3. Map keys to levels
        4. Deduplicate by ElementId
        5. Store results on ext_data_silo

        Args:
            ext_data_silo: ExtendedDataSilo instance
            ext_level_collection: ExtendedLevelCollection instance

        Returns:
            list: ExtendedLevel objects where program volumes should exist
        """
        data_silo_uid = (
            ext_data_silo.parameters["PDSO - Program UID"].value or "UNKNOWN"
        )

        # 1. Get canonical level (always include regardless of mapping)
        canonical_level_id = ext_data_silo.rpw_element.LevelId.Value
        canonical_ext_level = next(
            (
                el
                for el in ext_level_collection.extended_levels
                if el.rpw_level.Id.Value == canonical_level_id
            ),
            None,
        )

        required_levels = []
        if canonical_ext_level:
            required_levels.append(canonical_ext_level)
            self.logger.debug(
                "Data Silo '{}' canonical level: {}".format(
                    data_silo_uid, canonical_ext_level.rpw_level.Name
                )
            )
        else:
            self.logger.error(
                "Data Silo '{}' canonical level not found (ElementId: {})".format(
                    data_silo_uid, canonical_level_id
                )
            )

        # 2. Parse stacking parameter
        stacking_param = ext_data_silo.parameters["PDSO - Program Stacking"].value
        if stacking_param and stacking_param.strip():
            self.logger.debug(
                "Data Silo '{}' stacking parameter: '{}'".format(
                    data_silo_uid, stacking_param
                )
            )

            keys, parsing_errors = self.parse_stacking_parameter(
                stacking_param, data_silo_uid
            )

            self.logger.debug(
                "DEBUG: Data Silo '{}' parsed keys from stacking parameter: {}".format(
                    data_silo_uid, keys
                )
            )

            # Store parsing errors on data silo
            ext_data_silo.parsing_errors = parsing_errors

            if keys:
                # 3. Map keys to levels
                stacked_levels, missing_keys = self.map_keys_to_levels(
                    keys, ext_level_collection
                )

                self.logger.debug(
                    "DEBUG: Data Silo '{}' mapped stacked_levels (count={}): {}".format(
                        data_silo_uid,
                        len(stacked_levels),
                        [
                            (el.rpw_level.Name, el.rpw_level.Id.Value)
                            for el in stacked_levels
                        ],
                    )
                )

                # Store missing mappings on data silo
                ext_data_silo.missing_level_mappings = missing_keys

                # Add to required levels
                required_levels.extend(stacked_levels)

        # 4. Deduplicate by ElementId (filter out any None values)
        self.logger.debug(
            "DEBUG: Data Silo '{}' required_levels before deduplication (count={}): {}".format(
                data_silo_uid,
                len(required_levels),
                [
                    (el.rpw_level.Name if el and el.rpw_level else "None")
                    for el in required_levels
                ],
            )
        )

        seen_ids = set()
        deduplicated = []
        for ext_level in required_levels:
            if ext_level is None or ext_level.rpw_level is None:
                self.logger.warning(
                    "Data Silo '{}' has None level in required_levels - skipping".format(
                        data_silo_uid
                    )
                )
                continue
            level_id = ext_level.rpw_level.Id.Value
            if level_id not in seen_ids:
                seen_ids.add(level_id)
                deduplicated.append(ext_level)

        # 5. Store on ext_data_silo
        ext_data_silo.required_levels = deduplicated

        if deduplicated:
            self.logger.debug(
                "Data Silo '{}' required levels after deduplication (count={}): {}".format(
                    data_silo_uid,
                    len(deduplicated),
                    ", ".join([el.rpw_level.Name for el in deduplicated]),
                )
            )
            self.logger.debug(
                "DEBUG: Data Silo '{}' final required_levels with IDs: {}".format(
                    data_silo_uid,
                    [(el.rpw_level.Name, el.rpw_level.Id.Value) for el in deduplicated],
                )
            )
        else:
            self.logger.warning(
                "Data Silo '{}' has no valid required levels".format(data_silo_uid)
            )

        return deduplicated

    def validate_all_stacking_parameters(
        self, ext_data_silo_collection, ext_level_collection
    ):
        """Validate all data silo stacking parameters before sync operations.

        Iterates all data silos, enumerates required levels, and aggregates
        parsing errors and missing mappings into a ValidationResult object.

        Args:
            ext_data_silo_collection: ExtendedDataSiloCollection instance
            ext_level_collection: ExtendedLevelCollection instance

        Returns:
            ValidationResult: Object with validation status and error details
        """
        self.logger.info(
            "Validating level mappings for {} data silos...".format(
                len(ext_data_silo_collection.extended_data_silos)
            )
        )

        result = ValidationResult()

        for ext_data_silo in ext_data_silo_collection.extended_data_silos:
            data_silo_uid = (
                ext_data_silo.parameters["PDSO - Program UID"].value or "UNKNOWN"
            )

            # Enumerate required levels (stores errors on ext_data_silo)
            self.enumerate_required_levels_for_data_silo(
                ext_data_silo, ext_level_collection
            )

            # Aggregate errors into result
            if ext_data_silo.parsing_errors:
                for error in ext_data_silo.parsing_errors:
                    result.add_parsing_error(data_silo_uid, error)

            if ext_data_silo.missing_level_mappings:
                result.add_missing_mappings(
                    data_silo_uid, ext_data_silo.missing_level_mappings
                )

        if result.passed:
            self.logger.info("Level mapping validation passed")
        else:
            self.logger.warning("Level mapping validation failed - see details below")
            self.logger.warning(result.get_summary_message())

        return result
