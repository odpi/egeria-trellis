/** Answers the app's in-page confirm dialog (confirm-dialog.js) for a test: watches the document, records each
 *  dialog's title and message in `asked` (as one string), and presses Confirm or Cancel. */
export function autoAnswerConfirm(document, { answer = true, asked = [] } = {}) {
  const win = document.defaultView;
  const obs = new win.MutationObserver(() => {
    document.querySelectorAll('[data-confirm-dialog]').forEach((box) => {
      if (box.dataset.answered) return;
      box.dataset.answered = '1';
      const t = box.querySelector('[data-dialog-title]').textContent;
      const m = box.querySelector('[data-dialog-message]').textContent;
      asked.push(`${t}\n\n${m}`);
      box.querySelector(answer ? '[data-confirm-yes]' : '[data-confirm-no]').click();
    });
  });
  obs.observe(document.body, { childList: true, subtree: true });
  return { asked, stop: () => obs.disconnect() };
}
