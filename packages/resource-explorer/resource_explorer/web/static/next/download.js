/* download.js -- hand a CSV the server produced to the browser as a file.
 *
 * The fetch half lives in re-api.js (`fetchCsvFile` and friends: bearer auth
 * rides on the patched fetch, which a plain <a href download> would not carry).
 * This is the DOM half, shared by every CSV export in /next: the Find
 * dialog's candidate table, an investigation's scope, a work list. */

/** Save `text` as `filename`. Returns true when the browser was handed the file;
 *  throws when it cannot start a download (no Blob URLs). */
export function saveCsv(text, filename) {
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    throw new Error('this browser cannot start a download');
  }
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}
