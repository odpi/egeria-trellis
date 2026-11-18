/* format.js moved to the shared package (packages/trellis-ui, served at /static/trellis-ui/format.js) with
 * envelope.js, which needs it. This re-export keeps RE's own imports of /static/next/format.js working, and the
 * browser still loads one copy: the module under /static/trellis-ui/. */
export * from '/static/trellis-ui/format.js';
