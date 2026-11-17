# -*- coding: utf-8 -*-
"""Observable wrapper class for configuration mapping items.

This module provides ConfigMappingItem class for managing key-value mappings
in the PDSO configuration UI with WPF data binding support.
"""

# System/Windows Modules
from System.ComponentModel import PropertyChangedEventArgs

# Python standard libraries
import re


class ConfigMappingItem(object):
    """Observable wrapper for configuration mapping key-value pairs.

    Provides INotifyPropertyChanged implementation for WPF data binding.
    Supports various mapping types:
    - Area scheme mappings (str -> str)
    - Level mappings (str -> ElementId)
    - Generic model subcategory mappings (str -> str)

    Attributes:
        key: Mapping key (e.g., area scheme name, level identifier)
        value: Mapping value (e.g., parameter name, level ElementId)
        parent_manager: Reference to parent manager for callbacks
    """

    def __init__(self, key="", value=None, parent_manager=None):
        """Initialize mapping item with key-value pair.

        Args:
            key (str): Mapping key
            value: Mapping value (type varies by mapping type)
            parent_manager: Optional parent manager reference
        """
        self.parent_manager = parent_manager

        # INotifyPropertyChanged implementation
        self._property_changed_handlers = []

        # Backing fields for properties
        self._key = key
        self._value = value

    # INotifyPropertyChanged implementation
    def add_PropertyChanged(self, handler):
        """Add handler for PropertyChanged event.

        Args:
            handler: Event handler for property changes
        """
        self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        """Remove handler for PropertyChanged event.

        Args:
            handler: Event handler to remove
        """
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def on_property_changed(self, property_name):
        """Raise PropertyChanged event to notify UI.

        Args:
            property_name (str): Name of the property that changed
        """
        args = PropertyChangedEventArgs(property_name)
        for handler in self._property_changed_handlers:
            handler(self, args)

    # Observable properties
    @property
    def key(self):
        """Get mapping key.

        Returns:
            str: Current key value
        """
        return self._key

    @key.setter
    def key(self, value):
        """Set mapping key and notify UI.

        Args:
            value (str): New key value
        """
        if self._key != value:
            self._key = value
            self.on_property_changed("key")
            # Notify parent ViewModel of changes
            if self.parent_manager and hasattr(
                self.parent_manager, "has_unsaved_changes"
            ):
                if not self.parent_manager._is_initializing:
                    self.parent_manager.has_unsaved_changes = True

    @property
    def value(self):
        """Get mapping value.

        Returns:
            Mapping value (type varies)
        """
        return self._value

    @value.setter
    def value(self, new_value):
        """Set mapping value and notify UI.

        Args:
            new_value: New value (type varies)
        """
        if self._value != new_value:
            self._value = new_value
            self.on_property_changed("value")
            # Notify parent ViewModel of changes
            if self.parent_manager and hasattr(
                self.parent_manager, "has_unsaved_changes"
            ):
                if not self.parent_manager._is_initializing:
                    self.parent_manager.has_unsaved_changes = True

    @staticmethod
    def natural_sort_key(text):
        """Generate natural sort key for text with embedded numbers.

        Handles proper sorting of identifiers like:
        - "Lvl 01", "Lvl 02", "Lvl 10" (not "Lvl 1", "Lvl 10", "Lvl 2")
        - "Level 2", "Level 10", "Level 20"
        - "B1.03.A2", "B1.10.A1"

        Args:
            text (str): Text to generate sort key for

        Returns:
            list: Sort key with integers for numeric portions

        Example:
            >>> sorted(["Lvl 2", "Lvl 10", "Lvl 1"], key=ConfigMappingItem.natural_sort_key)
            ["Lvl 1", "Lvl 2", "Lvl 10"]
        """
        if not text:
            return []

        # Split text into alternating text/number chunks
        parts = re.findall(r"(\d+|\D+)", str(text))

        # Convert numeric chunks to integers for proper sorting
        result = []
        for part in parts:
            if part.isdigit():
                result.append(int(part))
            else:
                result.append(part.lower())  # Case-insensitive text comparison

        return result
