"""One device choice for every Trellis model, and a start-up report of the machine against the installed PyTorch.

docs/hardware-acceleration-proposal.md, Proposal A and §5a. Three places used to choose a device three ways (RE's
embeddings, EA's embeddings, Docling's guard against Apple's GPU); they all ask `pick_device` now, and each run can
say what it ran on and why.

    pick_device(requested="auto", purpose="")   -> Choice(device, reason, gpu)
    hardware_report()                            -> {"machine", "build", "choice", "findings"}
    startup_line(report)                         -> one line for the start-up log

Order, for `requested == "auto"`: `TRELLIS_DEVICE` when set (cpu | cuda | rocm | mps | auto); else an NVIDIA or AMD
GPU (PyTorch reports both through its CUDA API; `torch.version.hip` says which); else Apple's GPU unless a known-bad
rule applies to this purpose; else the CPU. An app's own explicit setting (requested != "auto") wins over
`TRELLIS_DEVICE`. A device the installed PyTorch cannot use is refused with a sentence (`DeviceUnavailable`), never
quietly replaced.

Imports nothing heavy: torch is imported only when a choice needs it, and is never a dependency of this package.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any

__all__ = [
    "ALLOW_MPS_ENV",
    "DEVICE_ENV",
    "MPS_SAFE_TRANSFORMERS",
    "Choice",
    "DeviceUnavailable",
    "hardware_report",
    "mps_problem",
    "pick_device",
    "probe_machine",
    "report_once",
    "startup_line",
]

DEVICE_ENV = "TRELLIS_DEVICE"
#: Docling's own switch for Apple's GPU (kept from trellis-artifact-tree): 1/true/yes allows, anything else forbids.
ALLOW_MPS_ENV = "TRELLIS_DOCLING_ALLOW_MPS"
#: The first transformers whose RT-DETR layout models stop hard-coding float64 on Apple's GPU (gate b354).
MPS_SAFE_TRANSFORMERS = "5.22.2"
_DEVICES = ("cpu", "cuda", "mps")


class DeviceUnavailable(ValueError):
    """A device was asked for that the installed PyTorch cannot use. The message says why and what would."""


@dataclass(frozen=True)
class Choice:
    device: str            # "cpu" | "cuda" | "mps": what to hand to torch / sentence-transformers / Docling
    reason: str            # why, in words
    gpu: str | None = None   # the GPU's name, when one was chosen


def _torch():
    try:
        import torch
        return torch
    except Exception:  # noqa: BLE001 -- not installed, or broken: the CPU is all there is
        return None


def _version_at_least(installed: str, wanted: str) -> bool:
    try:
        from packaging.version import Version
        return Version(installed) >= Version(wanted)
    except Exception:  # noqa: BLE001 -- unparseable: not known to be safe
        return False


def mps_problem(purpose: str = "") -> str | None:
    """Why Apple's GPU must not be used for this purpose, or None. One rule today, for Docling: transformers older
    than 5.22.2 hard-coded float64 in the RT-DETR layout model, which Apple's GPU lacks (gate b354, 2026-11-11).
    `TRELLIS_DOCLING_ALLOW_MPS` decides it when set."""
    if purpose != "docling":
        return None
    override = os.environ.get(ALLOW_MPS_ENV, "").strip().lower()
    if override:
        return None if override in {"1", "true", "yes"} else f"{ALLOW_MPS_ENV}={override} forbids Apple's GPU"
    try:
        installed = metadata.version("transformers")
    except metadata.PackageNotFoundError:
        return "transformers is not installed, so it is not known to handle Apple's GPU"
    if _version_at_least(installed, MPS_SAFE_TRANSFORMERS):
        return None
    return (f"transformers {installed} predates {MPS_SAFE_TRANSFORMERS}, whose layout model hard-codes float64, which "
            f"Apple's GPU lacks")


def _gpu_name(torch) -> str | None:
    try:
        return torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        return None


def _available(torch, device: str) -> bool:
    if torch is None:
        return device == "cpu"
    if device == "cuda":
        return bool(torch.cuda.is_available())
    if device == "mps":
        return bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    return True


def pick_device(requested: str = "auto", *, purpose: str = "") -> Choice:
    """The device for `purpose` ("embeddings", "docling", ...). `requested` is the app's own setting."""
    asked = (requested or "auto").strip().lower()
    source = "this app's setting"
    if asked == "auto":
        env = os.environ.get(DEVICE_ENV, "").strip().lower()
        if env and env != "auto":
            asked, source = env, DEVICE_ENV
    if asked == "rocm":   # PyTorch reports AMD GPUs through the CUDA API
        asked = "cuda"
    torch = _torch()
    if asked != "auto":
        if asked not in _DEVICES:
            raise DeviceUnavailable(f"{source} asks for device {asked!r}; it can be cpu, cuda, rocm, mps or auto")
        if not _available(torch, asked):
            have = "no PyTorch" if torch is None else f"PyTorch {torch.__version__}"
            raise DeviceUnavailable(f"{source} asks for {asked}, which {have} cannot use on this machine")
        if asked == "mps" and mps_problem(purpose):
            raise DeviceUnavailable(f"{source} asks for mps, but {mps_problem(purpose)}")
        return Choice(asked, f"{source} asks for {asked}", _gpu_name(torch) if asked == "cuda" else None)
    if torch is None:
        return Choice("cpu", "PyTorch is not installed")
    if torch.cuda.is_available():
        name = _gpu_name(torch)
        kind = f"AMD (ROCm {torch.version.hip})" if getattr(torch.version, "hip", None) else "NVIDIA (CUDA)"
        return Choice("cuda", f"an {kind} GPU is available: {name or 'unnamed'}", name)
    if _available(torch, "mps"):
        problem = mps_problem(purpose)
        if problem:
            return Choice("cpu", f"Apple's GPU is available, but {problem}")
        return Choice("mps", "Apple's GPU is available", "Apple GPU (MPS)")
    return Choice("cpu", "no GPU this PyTorch can use")


# ── the machine, without PyTorch ─────────────────────────────────────────────────────────────────────────────

KFD_NODES = Path("/sys/class/kfd/kfd/topology/nodes")
NVIDIA_PROC = Path("/proc/driver/nvidia/gpus")


def _gfx(target_version: int) -> str:
    """kfd's gfx_target_version (e.g. 110500, 90010) as the LLVM target name (gfx1150, gfx90a)."""
    major, minor, step = target_version // 10000, (target_version // 100) % 100, target_version % 100
    return f"gfx{major}{minor:x}{step:x}"


def _probe(status: str, gpus: list[str] | None = None, reason: str = "") -> dict[str, Any]:
    """One vendor's probe: "found" (with gpus), "none" (the OS says there is none), or "not checked" (with reason)."""
    return {"status": status, "gpus": list(gpus or []), "reason": reason}


def _probe_nvidia() -> dict[str, Any]:
    try:
        if NVIDIA_PROC.is_dir():
            models = []
            for info in sorted(NVIDIA_PROC.glob("*/information")):
                models.append(next((ln.split(":", 1)[1].strip() for ln in info.read_text(errors="ignore").splitlines()
                                    if ln.startswith("Model:")), "NVIDIA GPU"))
            return _probe("found", models) if models else _probe("none", reason="NVIDIA's driver lists no GPU")
        if shutil.which("nvidia-smi"):
            run = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=3, check=False)
            if run.returncode != 0:   # a failing nvidia-smi says nothing about whether there is a GPU
                why = " ".join((run.stderr or run.stdout or "").split())[:160]
                return _probe("not checked", reason=f"nvidia-smi failed (exit {run.returncode}): {why}")
            models = [ln.split(":", 1)[1].split("(")[0].strip() for ln in run.stdout.splitlines() if ":" in ln]
            return _probe("found", models) if models else _probe("none", reason="nvidia-smi lists no GPU")
        return _probe("none", reason="no NVIDIA driver (/proc/driver/nvidia) and no nvidia-smi")
    except Exception as exc:  # noqa: BLE001 -- a probe never raises; it says what it could not do
        return _probe("not checked", reason=f"{type(exc).__name__}: {exc}")


def _probe_amd() -> dict[str, Any]:
    try:
        if not KFD_NODES.is_dir():
            return _probe("none", reason="no AMD kernel driver topology (/sys/class/kfd)")
        gfx = []
        for props in sorted(KFD_NODES.glob("*/properties")):
            for ln in props.read_text(errors="ignore").splitlines():
                if ln.startswith("gfx_target_version"):
                    v = int(ln.split()[-1] or 0)
                    if v:
                        gfx.append(_gfx(v))
        return _probe("found", gfx) if gfx else _probe("none", reason="the kfd topology lists no GPU node")
    except Exception as exc:  # noqa: BLE001 -- a probe never raises; it says what it could not do
        return _probe("not checked", reason=f"{type(exc).__name__}: {exc}")


def probe_machine() -> dict[str, Any]:
    """The GPUs the machine has, read from the OS: NVIDIA from its driver (or nvidia-smi), AMD from the kernel's kfd
    topology, Apple from the platform. Each is {"status": found | none | not checked, "gpus", "reason"}; nothing here
    raises. No PyTorch, no network, nothing installed."""
    return {"platform": f"{sys.platform}/{platform.machine()}", "nvidia": _probe_nvidia(), "amd": _probe_amd(),
            "apple": sys.platform == "darwin" and platform.machine() == "arm64"}


def _build() -> dict[str, Any]:
    torch = _torch()
    if torch is None:
        return {"torch": None}
    hip = getattr(torch.version, "hip", None)
    return {"torch": torch.__version__, "cuda": getattr(torch.version, "cuda", None), "hip": hip,
            "kind": "rocm" if hip else ("cuda" if getattr(torch.version, "cuda", None) else "cpu"),
            "gpu_available": bool(torch.cuda.is_available()), "mps_available": _available(torch, "mps")}


def _choices(purposes: dict[str, str]) -> dict[str, dict[str, Any]]:
    out = {}
    for purpose, requested in purposes.items():
        try:
            out[purpose] = {"setting": requested, **asdict(pick_device(requested, purpose=purpose))}
        except DeviceUnavailable as exc:
            out[purpose] = {"setting": requested, "device": None, "reason": str(exc), "gpu": None}
    return out


def hardware_report(purposes: dict[str, str] | None = None) -> dict[str, Any]:
    """What the machine has, what the installed PyTorch can use, the device each purpose actually gets (from the app's
    own setting for it: `purposes` = {"embeddings": "cpu", "docling": "auto", ...}), and findings that name the fix.
    Reports only: it never installs or changes anything, and it never raises (a failure is a finding)."""
    purposes = dict(purposes or {"default": "auto"})
    try:
        machine, build = probe_machine(), _build()
        choices = _choices(purposes)
    except Exception as exc:  # noqa: BLE001 -- the report is about the machine; it must never take the app down
        return {"machine": None, "build": None, "choices": {},
                "findings": [{"level": "warn", "text": f"the hardware report could not be made: "
                                                       f"{type(exc).__name__}: {exc}"}]}
    findings: list[dict[str, str]] = []
    kind = build.get("kind")
    amd, nvidia = machine["amd"], machine["nvidia"]
    proposal = "see docs/hardware-acceleration-proposal.md"
    for vendor, probe in (("NVIDIA", nvidia), ("AMD", amd)):
        if probe["status"] == "not checked":
            findings.append({"level": "note", "text": f"{vendor} GPUs not checked: {probe['reason']}"})
    if build.get("torch") is None:
        findings.append({"level": "note", "text": "PyTorch is not installed; models that need it cannot run here"})
    if amd["status"] == "found" and kind != "rocm" and build.get("torch"):
        findings.append({"level": "warn", "text": f"AMD GPU {', '.join(amd['gpus'])} found, but this environment "
                         f"has the {kind.upper() if kind == 'cuda' else 'CPU-only'} PyTorch; a ROCm build would make "
                         f"PDF conversion about 2x faster (measured on a Radeon 890M; {proposal})"})
    if nvidia["status"] == "found" and kind != "cuda" and build.get("torch"):
        findings.append({"level": "warn", "text": f"NVIDIA GPU {', '.join(nvidia['gpus'])} found, but this "
                         f"environment has the {'ROCm' if kind == 'rocm' else 'CPU-only'} PyTorch; the CUDA build "
                         f"would use it ({proposal})"})
    if kind == "cuda" and nvidia["status"] == "none" and not build.get("gpu_available"):
        findings.append({"level": "note", "text": "this PyTorch is the CUDA build (about 4.5 GB of NVIDIA libraries) "
                         f"and the machine has no NVIDIA GPU; the CPU-only build would do the same work ({proposal})"})
    if kind == "rocm" and amd["status"] == "none" and not build.get("gpu_available"):
        findings.append({"level": "note", "text": "this PyTorch is the ROCm build and the machine has no AMD GPU it "
                         "can see; the CPU-only build would do the same work"})
    matched = (kind == "rocm" and amd["status"] == "found") or (kind == "cuda" and nvidia["status"] == "found")
    if matched and not build.get("gpu_available"):
        findings.append({"level": "warn", "text": "the GPU and this PyTorch build match, but PyTorch cannot use the "
                         "GPU (driver, or permission on /dev/kfd or /dev/nvidia*)"})
    for purpose, c in choices.items():
        if c["device"] is None:
            findings.append({"level": "warn", "text": f"{purpose}: {c['reason']}"})
    return {"machine": machine, "build": build, "choices": choices, "findings": findings}


@cache
def _report_for(purposes: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    return hardware_report(dict(purposes))


def report_once(purposes: dict[str, str] | None = None) -> dict[str, Any]:
    """The report for these purposes, made once per process (at start-up): serving it again costs nothing and never
    touches PyTorch. hardware_report never raises, so what is cached is always a report."""
    return _report_for(tuple(sorted((purposes or {"default": "auto"}).items())))


report_once.cache_clear = _report_for.cache_clear   # type: ignore[attr-defined]


def startup_line(report: dict[str, Any]) -> str:
    """One line for the start-up log: the PyTorch build, each purpose's device and why, and any finding."""
    b = report.get("build") or {}
    torch_word = f"PyTorch {b['torch']} ({b.get('kind')})" if b.get("torch") else "no PyTorch"
    parts = [f"hardware: {torch_word}"]
    for purpose, c in (report.get("choices") or {}).items():
        parts.append(f"{purpose} (setting {c['setting']}): {c['device'] or 'none'}, {c['reason']}")
    line = "; ".join(parts)
    for f in report.get("findings") or []:
        line += f"; {f['level'].upper()}: {f['text']}"
    return line
