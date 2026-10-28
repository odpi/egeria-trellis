"""Secondary source pin for STAGE-SUBTAB-RESET: the behaviour is proven by
frontend-build/test-harness/stage-subtab-reset.test.mjs; this only guards that
the router and the tab bar keep sharing ONE visibility list."""
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next" / "app.js").read_text(encoding="utf-8")


def test_tab_bar_and_router_share_one_list():
    assert "export function visibleSubTabs()" in APP
    assert APP.count("visibleSubTabs()") >= 3  # definition, subTabsHtml, reconcile
    assert "${visibleSubTabs().map(" in APP


def test_loadpane_reconciles_before_subtab_branches():
    body = APP[APP.index("async function loadPane()"):]
    r = body.index("reconcileSubTabForStage()")
    assert r < body.index("if (state.subTab === 'context') { await loadContextPane()")
    assert r > body.index("await renderInvestigation();")
