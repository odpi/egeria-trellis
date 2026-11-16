/* Studio's look picker: trellis-ui's, with no activity log and no "Tell us". */
import { configureLook, wireLook } from '/static/trellis-ui/look.js';
configureLook({ recordUrl: null, feedback: false });
const host = document.querySelector('[data-look-host]');
if (host) wireLook(host);
