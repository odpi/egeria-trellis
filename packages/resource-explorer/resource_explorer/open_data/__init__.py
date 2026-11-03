"""Open data (slice OD1): portals, portal directories, search and import into a work list.

Design: docs/design-notes/DESIGN-OPEN-DATA-SEARCH-AND-SURVEY.md §1-§3, §8, §9 (OD1 row), §11.
Nothing here surveys, profiles or publishes, and nothing here writes to Egeria. The one Egeria call is the
read-first identifier match (`match.py`).
"""
