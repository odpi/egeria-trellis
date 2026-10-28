/** Understanding's tabs read lazily: a section reads when its tab opens. A test that asserts a whole page opens
 *  the other two tabs first, the way a person would. */
export async function openAllUnderstandingTabs(document, wait = 300) {
  // and ends back on At a glance, where the stage opens
  for (const tab of ['understanding_contents', 'understanding_over_time', 'understanding_glance']) {
    const btn = document.querySelector(`[data-subtab="${tab}"]`);
    if (btn) { btn.click(); await new Promise((r) => setTimeout(r, wait)); }
  }
}
