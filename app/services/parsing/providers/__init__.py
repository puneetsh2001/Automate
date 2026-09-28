"""Utility-specific parsers for layouts the generic label parser can't read reliably.

Each parser extends GenericBillParser (so simple "Label: value" fields still
come from the shared alias logic) and overrides the fields whose layout is
specific to that utility, typically multi-register meter tables.
"""

from app.services.parsing.providers.apdcl import APDCLParser
from app.services.parsing.providers.karnataka_escom import KarnatakaEscomParser
from app.services.parsing.providers.rajasthan_discom import RajasthanDiscomParser

__all__ = ["APDCLParser", "KarnatakaEscomParser", "RajasthanDiscomParser"]
