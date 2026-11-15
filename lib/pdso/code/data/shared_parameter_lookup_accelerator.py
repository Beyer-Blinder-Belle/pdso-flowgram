# -*- coding: utf-8 -*-
"""PDSO Parameter Lookup - Performance optimization for parameter lookups.

This module provides a lookup accelerator that caches shared parameters
to significantly improve performance when looking up multiple parameters.
"""

# 3rd Party Modules
from rpw import db, DB


class Shared_Parameter_Lookup_Accelerator(object):
    """Performance utility that caches shared parameters for fast lookups.

    This class catalogs all Shared Parameters in the document on initialization,
    storing them in a dictionary where the key is each Shared Parameter's GUID.
    This significantly speeds up lookups of multiple shared parameters as opposed
    to looking them up one at a time.

    Attributes:
        binding_map: Revit ParameterBindings map from the document
        collection_of_shared_params_lookedup (dict): Dictionary mapping GUID to SharedParameterElement

    Example:
        accelerator = Shared_Parameter_Lookup_Accelerator(doc)
        param = accelerator.lookup_shared_param(my_guid)
        if param:
            print("Found parameter: {}".format(param.Name))
    """

    def __init__(self, doc):
        """Initialize the lookup accelerator with all shared parameters in the document.

        Args:
            doc: Revit Document instance to catalog shared parameters from

        Raises:
            Exception: If unable to access document parameter bindings or shared parameters
        """
        # Store the parameter bindings map for later use
        self.binding_map = doc.ParameterBindings

        # Initialize the lookup cache
        self.collection_of_shared_params_lookedup = {}

        # Get all shared parameters in the document
        shared_parameters = db.Collector(
            of_class=DB.SharedParameterElement,
            is_not_type=True,
        ).get_elements(wrapped=True)

        # Cache them by GUID for fast lookup
        self.collection_of_shared_params_lookedup = {
            sp.GuidValue: sp for sp in shared_parameters
        }

    def lookup_shared_param(self, parameter_guid):
        """Look up a shared parameter by GUID with caching.

        If GUID exists in cache, return cached object. Otherwise look it up,
        add it to cache, then return the object.

        Args:
            parameter_guid (Guid): System.Guid of the shared parameter to look up

        Returns:
            SharedParameterElement: The matching shared parameter element, or None if not found
        """
        # Check cache first
        if parameter_guid in self.collection_of_shared_params_lookedup:
            return self.collection_of_shared_params_lookedup[parameter_guid]

        # Not in cache, look it up
        shared_parameter = db.Collector(
            of_class=DB.SharedParameterElement,
            is_not_type=True,
            where=lambda shared_param: shared_param.GuidValue == parameter_guid,
        ).get_first()

        # Cache the result (even if None) to avoid repeated lookups
        self.collection_of_shared_params_lookedup[parameter_guid] = shared_parameter
        return shared_parameter
