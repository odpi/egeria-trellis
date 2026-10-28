"""Run curation_coverage from a script: python curation_coverage.py <ods-url> <database-slug>"""
import json
import sys

from trellis_analytics.readers.ods import OdsReader
from trellis_analytics.routines import curation_coverage

r = curation_coverage(OdsReader(sys.argv[1]), "database", sys.argv[2])
print(r.envelope.result_state.words, "·", r.summary)
print(json.dumps(r.to_dict(), indent=2))
