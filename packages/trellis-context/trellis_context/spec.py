"""What a context should contain — declared, not assembled ad hoc.

A ContextSpec is the compile target. It says which sections exist, how important
each is, how far each may be compressed, and which may be dropped. It does NOT
say what goes in them: resolvers answer that, and the packer never calls one.

Two fields carry the design's sharpest distinctions and are easy to conflate:

`mode` -- rank or gate. Purpose ranks: it orders what comes first and hides
nothing. But docs/investigation-framing-design.md §4 gates RFA emission on
Purpose, and warns that implementing that gate as a ranking floods the drawer
and fails SILENTLY. Making the distinction a typed field means it can be tested
rather than remembered.

`weight` -- budget share, and where Perspective belongs. Perspective was measured
and cannot discriminate: its twelve sets are strictly nested, so it varies the
SIZE of a result and never its content. That disqualifies it as a filter and
makes it ideal here, because nesting guarantees no perspective can be starved of
something only it needed -- there is nothing only-its. A weight cannot drop a
section; only the budget can, and then only an optional one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from trellis_artifact_tree.model import Rung


@dataclass(frozen=True)
class Section:
    """One part of a context, and the rules for fitting it in."""

    key: str
    #: What this section is FOR. Not decoration: a packer drops evidence before
    #: instructions, because a context with no instructions is not a smaller
    #: context, it is a different task.
    role: str = "evidence"
    #: Budget share relative to other sections. Set from Perspective.
    weight: float = 1.0
    #: Required sections are never dropped. If one cannot fit even at its
    #: coarsest allowed rung, the compile FAILS rather than returning a context
    #: missing something declared essential.
    required: bool = False
    #: "rank" orders; "gate" excludes. See the module docstring.
    mode: str = "rank"
    #: The coarsest rung this section may degrade to before it is dropped
    #: instead. A section whose IDENTIFIERS rung is meaningless (a diff, say)
    #: sets this to SUMMARY and is dropped rather than reduced to noise.
    floor: Rung = Rung.IDENTIFIERS
    #: Sections sharing a group are packed symmetrically -- equal budget, same
    #: rung. This is how comparison avoids favouring whichever subject packed
    #: first (docs/context-compilation-design.md §6). The shared rung respects
    #: every member's `floor`: if the group fits only below one member's floor,
    #: the whole group is dropped (or the compile fails, if a member is required).
    group: str = ""

    def __post_init__(self) -> None:
        if self.mode not in ("rank", "gate"):
            raise ValueError(f"{self.key}: mode must be 'rank' or 'gate', got {self.mode!r}")
        if self.weight <= 0:
            raise ValueError(f"{self.key}: weight must be positive, got {self.weight}")


@dataclass(frozen=True)
class ContextSpec:
    """A named, versioned compile target.

    `as_of` and `target_model` are both IDENTITY-BEARING, in the sense of
    Egeria Advisor's "part of spec identity?" test: change either and you get
    different content, so both belong in a cache key. `target_model` is easy to
    misfile as operational tuning -- it is not, because the model's context
    window sizes the budget, so a different model packs differently.
    """

    spec_id: str
    version: int
    sections: tuple[Section, ...]
    as_of: str = ""
    target_model: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        keys = [s.key for s in self.sections]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"{self.spec_id}: duplicate section key(s) {dupes}")

    def identity(self) -> tuple:
        """The part of this spec that changes the OUTPUT, for cache keying.

        Excludes `metadata`, which is annotation. Includes target_model, for
        the reason given in the class docstring.
        """
        return (self.spec_id, self.version, self.as_of, self.target_model,
                tuple((s.key, s.weight, s.required, s.mode, int(s.floor), s.group)
                      for s in self.sections))

    def by_key(self) -> dict[str, Section]:
        return {s.key: s for s in self.sections}

    def groups(self) -> dict[str, list[Section]]:
        out: dict[str, list[Section]] = {}
        for s in self.sections:
            if s.group:
                out.setdefault(s.group, []).append(s)
        return out
