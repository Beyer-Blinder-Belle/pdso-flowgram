# Python standard libraries

# System Modules

# 3rd Party Modules
from pyrevit import script

# Custom Modules


class ExtendedLevel(object):
    """
    Wrapper for rpw.Element (Level) objects with additional PDSO-specific properties.
    """

    def __init__(self, rpw_level):
        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        # Not needed

        # Identity/ Lookup Properties
        # Not needed

        # Wrapped Element or Collection
        # Not needed

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        # Not needed

        # Init Logic
        # Not needed

        # Configuration properties
        # self.doc = doc

        # Properties
        self.rpw_level = rpw_level

        # self.extended_levels = self.get_extended_levels()
