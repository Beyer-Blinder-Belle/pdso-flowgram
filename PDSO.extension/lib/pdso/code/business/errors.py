# -*- coding: utf-8 -*-
"""PDSO errors that a caller can correct."""


class PDSOError(Exception):
    """An error with a message for the caller and an HTTP-style status code.

    Attributes:
        status (int): 400 (bad input), 404 (not found) or 409 (model not ready)
    """

    def __init__(self, message, status=400):
        Exception.__init__(self, message)
        self.status = status
