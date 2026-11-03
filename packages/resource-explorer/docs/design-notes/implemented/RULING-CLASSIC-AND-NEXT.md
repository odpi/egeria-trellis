# What may diverge between the classic panel and `/next`

**Ruling** · settles the open question in `REVIEW-VERDICT-RULING.md` §3
**Date:** 2026-09-17 · **Read against:** `main` at `1b370cbe`

---

## 1 · The decision, and what it rules out

**Decision (project owner, 2026-09-17):** the classic panel will be retired only
if `/next` gets more traction.

So there is **no timeline**, and the conditional runs the opposite way from how I
had been reasoning. I had been treating classic as legacy on its way out, which
made a divergence a temporary embarrassment. It is not temporary. Classic is the
UI people are using now, and it stays until `/next` earns its replacement.

That disposes of the escape hatch I offered in the review — *"neither is urgent if
the classic panel is being retired on a known timeline."* It is not, so **the
one-clause fix in §3(a) is required, not optional**, and `#107`'s defect is live
in the interface people actually use.

## 2 · The rule I had was wrong, and the distinction it was missing

My standing rule: *a parallel UI may defer any affordance; it may not silently
omit one.* That was written with classic as the reference and `/next` as the
newcomer catching up. The decision above reverses the direction of travel, and in
doing so exposes that the rule conflates two things:

| | may it live only in `/next`? |
|---|---|
| **capability** — a thing the interface can now *do*: the two-column proposal display, the agreement line, the ports rail, the diagram caption | **Yes.** This is the point. Capability only in `/next` is exactly what creates the reason to move. |
| **honesty** — whether what a surface *does* show is true | **No, never.** If classic shows a thing at all, it must not show it falsely. |

**A missing feature is a reason to switch. A wrong answer is a reason to distrust
the product.** A strategy of "improve `/next` until people move" works on the
first and is destroyed by the second: every ruling that lands only in `/next`
makes classic less *capable*, which is intended — but where it also leaves
classic saying something untrue, it makes the whole product less trustworthy,
which is not.

So the rule becomes:

> **A parallel UI may defer any affordance. Neither surface may state something
> false, and a divergence that turns into a false statement is not a deferral —
> it is a defect in the surface that kept the old answer.**

The test is one question, asked of every ruling from here: **does the classic
panel show this thing?** If it does not, `/next` may have it alone and nothing is
owed. If it does, classic must be left truthful — which is usually one clause,
far short of parity.

## 3 · What that means for the verdict ruling

`repo_survey_definition_adapter.py:2951` keeps
`max(comp_rows, key=lambda r: r["surveyed_at"])` as the top-level primary, and
the classic panel's `_archRow` renders it. Classic **does** show a component's
type and confidence, so by §2 it must not show them falsely — and presenting one
of two current proposals as the answer is false.

**Build the clause, not the parity.** Where a path carries more than one current
proposal, the classic row gains *also proposed by coupling ›*. The two-column
display, the agreement line and the withdrawal flag stay `/next`-only, and they
are three of the reasons to move.

**And change the primary.** Where a caller can take only one proposal, it should
get the best-evidenced — agreed first, then highest confidence — not the most
recent. *Most recently surveyed* is a fact about the scheduler, not about the
component, and it is the specific thing §2a set out to remove. Leaving it as the
fallback keeps the arbitrary answer in the one surface least able to caveat it.

## 4 · Superseded — the traction question was the second one

*This section asked what would have to be counted for "if `/next` gets more
traction" to be answerable. That was premature.*

**Decision (project owner, 2026-09-17):** the two cannot be compared until
`/next` is feature complete, and it is missing features present in classic that
have never been discussed.

So the prior question is not how to measure traction but **what classic does that
`/next` does not** — which nobody has written down, which leaves "feature
complete" undefined and traction meaningless until it is. See
`SPEC-PARITY-INVENTORY-AND-GROUPS.md`, which also carries the first entry:
resource groups render in `/next` but do not collapse.

The §2 rule above is unaffected: capability may diverge, honesty may not.
