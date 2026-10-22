"""Krisha.kz data source: polite cached client, HTML parsers, address geocoding, search runner.

Only public pages are used (/prodazha|arenda/kvartiry/astana/ and /a/show/{id}), nothing
that robots.txt disallows, no personal data (seller names, phones, free text), and the
site's bot check is respected, never worked around.
"""
from .client import Blocked, KrishaClient
from .geocode import Geocoder
from .search import SearchQuery, collect

__all__ = ["Blocked", "KrishaClient", "Geocoder", "SearchQuery", "collect"]
