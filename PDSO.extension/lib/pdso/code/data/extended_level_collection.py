# Python standard libraries

# System Modules

# 3rd Party Modules
from rpw import db, DB
from pyrevit import script

# Custom Modules
from pdso.code.data.extended_level import ExtendedLevel


class ExtendedLevelCollection(object):
    """
    Wrapper for rpw.Element (Level) objects with additional PDSO-specific properties.
    """

    def __init__(self, doc):
        # Output
        self.logger = script.get_logger()
        self.output = script.get_output()

        # Document (if needed)
        self.doc = doc

        # Identity/ Lookup Properties
        # Not needed

        # Wrapped Element or Collection
        self.extended_levels = []

        # Hashing for integrity checks
        self._hash = None

        # PDSO-specific properties and correlations
        # Not needed

        # Init Logic
        self.logger.debug("Initializing ExtendedLevelCollection")

        self.extended_levels = self.get_extended_levels()

    def get_extended_levels(self):
        """Retrieve all Level instances in the model as extended Level objects."""
        levels_list = db.Collector(of_class=DB.Level).get_elements()

        extended_levels = [ExtendedLevel(level) for level in levels_list]

        return extended_levels
