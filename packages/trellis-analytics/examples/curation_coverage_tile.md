# Curation Coverage tile

The Portal tile for trellis-analytics `curation_coverage` (brief 2d), written as a Dr.Egeria report spec document.
The Advisor's report-spec parser reads it to the same FormatSet as `trellis_analytics.portal.report_specs()`.

## Create Report Spec
### Heading
Curation Coverage

### Description
Published against not yet published, from the curation record (trellis-analytics curation_coverage). The subtitle is the result's state; counts carry the ids they count.

### Family
Curation

### Output Types
DICT, JSON, BAR

### Analytic Function
curation_coverage_tile

### Analytic Spec Params
resource_type=database
