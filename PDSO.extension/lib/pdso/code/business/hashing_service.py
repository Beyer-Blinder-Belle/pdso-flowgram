from pyrevit import DB, script
import json
import hashlib


class PDSOHashingService(object):
    """
    Service class for handling PDSO hashing operations.

    Implements singleton pattern.
    Use get_instance() to obtain the service instance.
    """

    _instance = None

    def __init__(self, tolerance=256):
        """
        Initialize the hash and compare service.

        Note: Use get_instance() instead of direct instantiation.

        Args:
           tolerance: Tolerance in divisions per inch for quantization (default 256 ~ 1/256")
        """

        self.logger = script.get_logger()
        self.output = script.get_output()

        self.tolerance = 1.0 / (tolerance * 12.0)  # Default is ~1/256" in feet;

    @classmethod
    def get_instance(cls, tolerance=256):
        """
        Get or create singleton instance.

        Args:
            tolerance: Tolerance in divisions per inch (used only on first instantiation)

        Returns:
            PDSOHashingService: Singleton instance

        Example:
            service = PDSOHashingService.get_instance()
            hash_value = service.area_tessellation_signature(area)
        """
        if cls._instance is None:
            cls._instance = cls(tolerance)

        return cls._instance

    @classmethod
    def clear_instance(cls):
        """
        Clear the cached instance.

        Call this when you need to force recreation with different settings.
        """
        cls._instance = None

    # Utilities -----------------------------------------------------
    def signatures_equal(self, sig_a, sig_b):
        return json.dumps(sig_a, sort_keys=True) == json.dumps(sig_b, sort_keys=True)

    def deserialize_signature(self, sig_json):
        return json.loads(sig_json)

    def signature_to_hash(self, signature):
        """
        Convert any signature dictionary to a hash string.
        Args:
            signature: Dictionary signature (e.g., from area_tessellation_signature)
        Returns:
            str: SHA256 hash of the signature
        """
        payload = json.dumps(signature, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # GEOMETRY -----------------------------------------------------
    def area_tessellation_hash(self, wrapped_spatial_element):
        """Get the hash string for a SpatialElement based on tessellated boundary curves.

        Args:
            wrapped_spatial_element: rpw.Element wrapped spatial element (Area, Room, etc.)

        Returns:
            str: SHA256 hash of the area tessellation signature
        """

        return self.signature_to_hash(
            self.area_tessellation_signature(wrapped_spatial_element)
        )

    def area_tessellation_signature(self, wrapped_spatial_element):
        """
        Build a signature for a SpatialElement (Area, Room, etc)
        based purely on tessellated boundary curves.

        Args:
            wrapped_spatial_element: Wrapped spatial element (Area, Room, etc.)
            return_full_signature (bool): If True, return full signature dict. If False, return hash hex string (default).

        Returns:
            str or dict: Hash hex string by default, or full signature dictionary if return_full_signature=True
        """
        logger = script.get_logger()
        boundaries = wrapped_spatial_element.unwrap().GetBoundarySegments(
            DB.SpatialElementBoundaryOptions()
        )

        curve_sigs = []

        for boundary in boundaries:
            for segment in boundary:
                curve = segment.GetCurve()
                curve_sigs.append(self.curve_tessellation_signature(curve))

        # Make order deterministic: sort by JSON string of each curve sig
        curve_sigs_sorted = sorted(
            curve_sigs, key=lambda s: json.dumps(s, sort_keys=True)
        )

        # Wrap in a "versioned" envelope so you can change structure later
        signature = {
            "method": "tessellation_v1",
            "curves": curve_sigs_sorted,
        }

        return signature

    def curve_tessellation_signature(self, curve):
        """
        Returns a JSON-serializable signature describing this curve
        by its tessellated points.
        """
        pts = curve.Tessellate()  # IList<XYZ>
        sig_pts = []

        last = None
        for p in pts:
            xyz = self.xyz_to_list(p)
            if xyz != last:  # drop immediate duplicates
                sig_pts.append(xyz)
                last = xyz

        return {
            "type": curve.GetType().Name,  # helpful for debugging
            "pts": sig_pts,
        }

    def xyz_to_list(self, p):
        q = self.q
        return [q(p.X), q(p.Y), q(p.Z)]

    def q(self, x, tol=None):
        if tol is None:
            tol = self.tolerance
        return round(x / tol) * tol

    # PARAMETER NAMES -----------------------------------------------------
    def parameter_names_hash(self, parameter_names):
        """Get the hash string for a list of parameter names.

        Args:
            parameter_names (list): List of parameter names (strings)

        Returns:
            str: SHA256 hash of the parameter names signature
        """
        return self.signature_to_hash(self.parameter_names_signature(parameter_names))

    def parameter_names_signature(self, parameter_names):
        """Build a signature dictionary for a list of parameter names.

        Args:
            parameter_names (list): List of parameter names (strings)

        Returns:\n            dict: Signature dictionary with method and normalized names
        """
        # Normalize: sort + lowercase (optional, but avoids accidental differences)
        norm = sorted([name.strip().lower() for name in parameter_names])

        signature = {"method": "parameter_names_v1", "names": norm}

        return signature

    # PARAMETER VALUES -----------------------------------------------------
    def parameter_values_hash(self, wrapped_element, parameter_names):
        """Get the hash string for an element based on parameter values.

        Args:
            wrapped_element: rpw.Element wrapped Revit element
            parameter_names (list): List of parameter names to include in signature

        Returns:
            str: SHA256 hash of the parameter values signature
        """
        return self.signature_to_hash(
            self.parameter_values_signature(wrapped_element, parameter_names)
        )

    def parameter_values_signature(self, wrapped_element, parameter_names):
        """Build a signature for an element based on parameter values.

        Args:
            wrapped_element: rpw.Element wrapped Revit element
            parameter_names (list): List of parameter names to include in signature

        Returns:
            dict: Signature dictionary with method and parameter key-value pairs
        """

        data = {}

        for name in parameter_names:
            p = wrapped_element.LookupParameter(name)
            if p:
                val = (
                    p.AsString()
                    if p.StorageType == DB.StorageType.String
                    else p.AsDouble()
                    if p.StorageType == DB.StorageType.Double
                    else p.AsInteger()
                    if p.StorageType == DB.StorageType.Integer
                    else p.AsElementId()
                )
            else:
                val = None

            data[name] = self.normalize_param_value(val)

        signature = {"method": "parameter_values_v1", "parameters": data}

        return signature

    def normalize_param_value(self, val):
        """
        Convert Revit parameter values into stable, comparable, JSON-friendly types.
        Handles None, numbers, strings, ElementId, etc.
        """
        if val is None:
            return None

        # ElementId => its integer value
        if isinstance(val, DB.ElementId):
            return val.Value

        # Numbers: quantize slightly to avoid floating noise
        if isinstance(val, float):
            return round(val, 4)

        # Everything else: convert directly
        return val
