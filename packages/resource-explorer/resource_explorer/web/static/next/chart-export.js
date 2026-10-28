/* Chart export — "Save image", one implementation for every chart.
 *
 * REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md §5: one control per
 * chart, Plotly's own `toImage`, and the provenance burned into the PNG as a
 * footer line — resource, chart title, run and source — because an exported
 * chart leaves the page and its evidence behind. Without the line it is a
 * picture of numbers nobody can trace.
 *
 * This module is deliberately independent of any stage: Understanding uses it
 * now; Scouting's chart can reuse it later (not wired in this slice). Nothing
 * here knows about a database. The footer is COMPOSED FROM THE RESPONSE'S OWN
 * PROVENANCE FIELDS by `provenanceFromResponse`, never from page state, so a
 * saved image says what the server said it read.
 */

/** The provenance a saved image carries, read from a chart route's response
 *  (`response.run.surveyed_at`, `response.run.source`). A missing run is
 *  written as "run unknown", never omitted: an image that hides what it came
 *  from is the thing this module exists to prevent. */
export function provenanceFromResponse(resource, title, response) {
  const run = (response && response.run) || null;
  return {
    resource: String(resource || ''),
    title: String(title || ''),
    run: run && run.surveyed_at ? String(run.surveyed_at) : '',
    source: run && run.source ? String(run.source) : '',
  };
}

/** The one footer line burned into the image. */
export function composeFooter({ resource, title, run, source }) {
  return [
    resource || 'resource unknown',
    title || 'chart unknown',
    `run ${run || 'unknown'}`,
    `source ${source || 'unknown'}`,
  ].join(' · ');
}

const FOOTER_PAD = 40;

/** A copy of a Plotly figure with the footer line added below the plot area.
 *  Pure: the figure the page is showing is not touched. */
export function figureWithFooter(figure, footer) {
  const data = JSON.parse(JSON.stringify((figure && figure.data) || []));
  const layout = JSON.parse(JSON.stringify((figure && figure.layout) || {}));
  const margin = Object.assign({ l: 56, r: 24, t: 24, b: 48 }, layout.margin || {});
  margin.b += FOOTER_PAD;
  layout.margin = margin;
  layout.annotations = (layout.annotations || []).concat([{
    text: footer,
    xref: 'paper', yref: 'paper', x: 0, y: 0,
    xanchor: 'left', yanchor: 'bottom',
    // paper y=0 is the bottom of the plot area; the image ends `margin.b`
    // below it, so shift down to sit just above the image's bottom edge.
    yshift: -margin.b + 6,
    showarrow: false,
    font: Object.assign({ size: 11 }, layout.font ? { color: layout.font.color, family: layout.font.family } : {}),
  }]);
  return { data, layout };
}

function anchorDownload(dataUrl, filename) {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Save one chart as a PNG with its provenance footer.
 *
 *  `figure`      the {data, layout} the page drew (themed layout included).
 *  `provenance`  from `provenanceFromResponse`.
 *  `Plotly`      the Plotly global (a parameter so tests and Scouting can
 *                pass their own).
 *  `deliver`     (dataUrl, filename) => void; default is a download link.
 *
 *  The footer is burned in by drawing the figure once more, off screen, with
 *  the footer annotation, and calling Plotly's own `toImage` on THAT — the
 *  visible chart is never changed or flickered. Returns {footer, filename,
 *  dataUrl}. Throws if Plotly is missing: a Save-image that silently does
 *  nothing is a blank pane in a smaller size. */
export async function saveChartImage({ Plotly, figure, provenance, deliver = anchorDownload,
                                       width = 1000, height = 520 }) {
  if (!Plotly || typeof Plotly.toImage !== 'function') {
    throw new Error('Plotly is not loaded, so the image cannot be saved');
  }
  const footer = composeFooter(provenance);
  const exportFig = figureWithFooter(figure, footer);
  const tmp = document.createElement('div');
  tmp.style.cssText = `position:fixed;left:-10000px;top:0;width:${width}px;height:${height}px`;
  document.body.appendChild(tmp);
  try {
    await Plotly.newPlot(tmp, exportFig.data, exportFig.layout,
      { displaylogo: false, staticPlot: true });
    const dataUrl = await Plotly.toImage(tmp, { format: 'png', width, height, scale: 2 });
    const slugify = (v) => String(v || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    const filename = `${slugify(provenance.resource)}-${slugify(provenance.title)}.png`;
    deliver(dataUrl, filename);
    return { footer, filename, dataUrl };
  } finally {
    try { if (Plotly.purge) Plotly.purge(tmp); } catch (_) { /* best effort */ }
    tmp.remove();
  }
}
