/* Resizable panes (owner rule: every pane width is user-resizable). A split is a flex row of panes with a gutter
 * between each pair; dragging a gutter sets the width of the pane to its left. Widths are remembered per split in
 * localStorage when storage works (it can throw: then they last this page). Pane content wraps (studio.css). */
import { h } from '/static/dom.js';

const KEY = (name) => `studio.panes.${name}`;
function load(name) { try { return JSON.parse(localStorage.getItem(KEY(name)) || 'null'); } catch (e) { return null; } }
function save(name, widths) { try { localStorage.setItem(KEY(name), JSON.stringify(widths)); } catch (e) {} }

/** makeSplit(name, [{el, width, min}]) -> the row element. The last pane takes the rest of the row. */
export function makeSplit(name, panes) {
  const row = h('div', { class: 'st-split', 'data-split': name });
  const remembered = load(name) || {};
  panes.forEach((p, i) => {
    p.el.classList.add('st-pane');
    const last = i === panes.length - 1;
    if (!last) {
      const w = remembered[i] || p.width || 320;
      p.el.style.flex = `0 0 ${w}px`;
    } else {
      p.el.style.flex = '1 1 0';
    }
    row.append(p.el);
    if (!last) {
      const gutter = h('div', { class: 'st-gutter', role: 'separator', 'aria-orientation': 'vertical',
                                tabindex: '0', title: 'Drag to resize' });
      gutter.addEventListener('pointerdown', (ev) => {
        ev.preventDefault();
        gutter.setPointerCapture(ev.pointerId);
        const startX = ev.clientX, startW = p.el.getBoundingClientRect().width, min = p.min || 160;
        const move = (e) => { p.el.style.flex = `0 0 ${Math.max(min, startW + e.clientX - startX)}px`; };
        const up = () => {
          gutter.removeEventListener('pointermove', move); gutter.removeEventListener('pointerup', up);
          const widths = load(name) || {};
          widths[i] = Math.round(p.el.getBoundingClientRect().width);
          save(name, widths);
        };
        gutter.addEventListener('pointermove', move); gutter.addEventListener('pointerup', up);
      });
      gutter.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
        const w = p.el.getBoundingClientRect().width + (e.key === 'ArrowRight' ? 24 : -24);
        p.el.style.flex = `0 0 ${Math.max(p.min || 160, w)}px`;
      });
      row.append(gutter);
    }
  });
  return row;
}
