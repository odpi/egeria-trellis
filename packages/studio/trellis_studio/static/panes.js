/* Resizable panes (owner rule: every pane width is user-resizable). A split is a flex row of panes with a gutter
 * between each pair; dragging a gutter sets the width of the pane to its left. Widths are remembered per split in
 * localStorage when storage works (it can throw: then they last this page). Pane content wraps (studio.css). */
import { h } from '/static/dom.js';

const KEY = (name) => `studio.panes.${name}`;
function load(name) { try { return JSON.parse(localStorage.getItem(KEY(name)) || 'null'); } catch (e) { return null; } }
function save(name, widths) { try { localStorage.setItem(KEY(name), JSON.stringify(widths)); } catch (e) {} }

/** makeSplit(name, [{el, width, min, grow}]) -> the row element. One pane grows (`grow: true`, else the last);
 * the others keep a width, and dragging the gutter next to a fixed pane resizes that pane. */
export function makeSplit(name, panes) {
  const row = h('div', { class: 'st-split', 'data-split': name });
  const remembered = load(name) || {};
  const g = panes.findIndex((p) => p.grow);
  const growAt = g >= 0 ? g : panes.length - 1;
  panes.forEach((p, i) => {
    p.el.classList.add('st-pane');
    // Fixed panes may shrink when the row is tight (flex-shrink 1); every pane keeps its minimum, so none collapses.
    p.el.style.flex = i === growAt ? '1 1 0' : `0 1 ${remembered[i] || p.width || 320}px`;
    p.el.style.minWidth = `${p.min || (i === growAt ? 280 : 160)}px`;
    row.append(p.el);
    if (i === panes.length - 1) return;
    // The gutter between i and i+1 resizes the fixed one of the two (the left, unless the left is the growing pane).
    const j = i === growAt ? i + 1 : i;
    const sign = j === i ? 1 : -1;
    const pane = panes[j];
    const gutter = h('div', { class: 'st-gutter', role: 'separator', 'aria-orientation': 'vertical',
                              tabindex: '0', title: 'Drag to resize' });
    const setW = (w) => { pane.el.style.flex = `0 1 ${Math.max(pane.min || 160, w)}px`; };
    gutter.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      gutter.setPointerCapture(ev.pointerId);
      const startX = ev.clientX, startW = pane.el.getBoundingClientRect().width;
      const move = (e) => setW(startW + sign * (e.clientX - startX));
      const up = () => {
        gutter.removeEventListener('pointermove', move); gutter.removeEventListener('pointerup', up);
        const widths = load(name) || {};
        widths[j] = Math.round(pane.el.getBoundingClientRect().width);
        save(name, widths);
      };
      gutter.addEventListener('pointermove', move); gutter.addEventListener('pointerup', up);
    });
    gutter.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      setW(pane.el.getBoundingClientRect().width + sign * (e.key === 'ArrowRight' ? 24 : -24));
    });
    row.append(gutter);
    pane.gutter = gutter;
  });
  return row;
}
