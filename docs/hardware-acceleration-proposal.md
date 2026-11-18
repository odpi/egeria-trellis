# Hardware acceleration: detect what a machine has, and install only what it can use

Proposal, 2026-11-11. Status: decided in principle by the project owner (§7), with conditions; rollout §6. Scope: Resource Explorer, Egeria Advisor and
the shared packages that run PyTorch models: Docling's PDF layout and table models, and sentence-transformers
embeddings.

## 1. Summary

Two separate problems need two separate mechanisms:

- **Which PyTorch build is installed** (CUDA for NVIDIA, ROCm for AMD, CPU-only) can only be decided when the
  environment is built. A running program cannot swap builds. Today a bare-metal Linux install (hedwig's shared
  environment, a developer's checkout) gets the lock's NVIDIA (CUDA 13) build and its 15 NVIDIA runtime packages,
  about 4.5 GB, whether or not the machine has an NVIDIA GPU. The Docker images do not: both Dockerfiles already
  swap to the CPU-only build after their last `uv sync` (§3.2). Proposal: one optional extra per build (`cpu`,
  `cuda`, `rocm`) chosen per machine, CPU-only by default on Linux; macOS unchanged (its standard build already
  includes Apple's GPU support). For the images this replaces the post-sync swap with the lock itself.
- **Which device each run uses** is decided at run time, and today three pieces of code decide it three different
  ways. Proposal: one small shared function that every consumer asks, which looks at what the installed PyTorch
  can actually use, refuses known-bad combinations, takes an environment override, and says what it chose and why.

Measured (§2): on hedwig the AMD GPU makes PDF conversion about **2.1 times faster** with **identical output**
(after a one-time compile); on laz Apple's GPU is about **1.3 times faster** overall, 1.7 times on everything but
the table-dense document.

## 2. What was measured

### 2.1 Hedwig: AMD Radeon 890M (Strix Point, gfx1150), 24 CPU cores

Docling 2.150 (RE's options: OCR off, table structure on), the same 113 pages of public sustainability PDFs in
every run, seconds per page:

| Sample | Pages | CPU, 4 threads (s/page) | GPU (ROCm 7.2), warm (s/page) | Speed-up |
|---|---|---|---|---|
| NAICS manual, front matter | 25 | 1.62 | 0.89 | 1.8× |
| NAICS manual, code pages | 25 | 0.95 | 0.36 | 2.6× |
| GWP tables | 9 | 1.02 | 0.45 | 2.2× |
| JRC emission factors (table-dense) | 13 | 11.0 | 5.5 | 2.0× |
| SPG methodology | 21 | 1.12 | 0.47 | 2.4× |
| Value-chain standard | 20 | 0.97 | 0.53 | 1.8× |
| **Total** | 113 | 259–271 s | **127 s** | **≈2.1×** |

- **Output:** the GPU's Markdown is word-for-word identical to the CPU's on all six samples.
- **First run:** the very first GPU run took 303 s, with two samples 4 times slower than the CPU, and a 40 s
  warm-up. ROCm compiles a kernel the first time it meets a new input shape and caches it on disk; a second, fresh
  process ran at the warm speed with a 7 s warm-up. So the cost is once per machine, not once per process.
- **Support:** `torch 2.13.0+rocm7.2` uses gfx1150 directly. The commonly needed `HSA_OVERRIDE_GFX_VERSION`
  workaround was not needed.
- **CPU threads are not a lever.** Docling uses 4 threads by default. At 16, light pages got 10–25% faster but the
  table-dense pages got slower (13.4 s/page against 11.0); the total did not improve (279 s).
- **Run-to-run noise** is about 4% (259 s and 271 s for the same CPU run).
- **Table-dense documents dominate** on every device: JRC is about 10 times any other sample. A GPU halves a
  rebuild's time; it does not change which documents are slow.

### 2.2 Laz: Apple silicon (MPS)

Docling on MPS failed on every page until transformers 5.22.2 fixed RT-DETR's hard-coded float64 (gate b354); with
the upgrade (#1057) it runs on MPS. The same script and 113 pages, run by the coordinator on laz on 2026-11-11 in
8813's environment (docling 2.150.0, transformers 5.22.2, torch 2.13.0, 4 threads), second runs:

| Sample | Pages | CPU (total s) | MPS (total s) | Speed-up |
|---|---|---|---|---|
| NAICS manual, front matter | 25 | 10.03 s | 7.03 s | 1.4× |
| NAICS manual, code pages | 25 | 5.87 s | 2.90 s | 2.0× |
| GWP tables | 9 | 2.31 s | 0.98 s | 2.4× |
| JRC emission factors (table-dense) | 13 | 36.80 s | 34.12 s | 1.1× |
| SPG methodology | 21 | 5.83 s | 2.84 s | 2.1× |
| Value-chain standard | 20 | 4.57 s | 2.77 s | 1.6× |
| **Total** | 113 | **65.4 s** | **50.6 s** | **≈1.3×** |

- **The table-dense document barely gains on MPS** and is 56% of the total on the CPU and 67% on MPS, so its cost
  is not model inference there. Without it, MPS is 1.7 times faster (16.52 s against 28.61 s).
- **First runs** were close to second runs (MPS 52.3 s, CPU 67.0 s): no large one-time compile on Apple's GPU.
- **Output:** identical on laz between MPS and CPU (43,206 words, the same characters and tables per sample).
- **Laz's CPU is about 4 times hedwig's** on this sample (65 s against 259–271 s), and table-dense pages are where
  the gap is largest (JRC 36.8 s against about 143 s).
- **The two machines detect tables differently.** On each machine every device gives the same output, but laz
  finds 6, 5, 2, 9, 6 and 4 tables in the six samples and hedwig 8, 0, 2, 9, 5 and 4, with nearly the same words
  (43,206 against 43,153). So a chat index rebuilt on one machine is not byte-identical to one rebuilt on the
  other, and a cross-machine comparison should compare words, not table counts.
- **The upgrade changed table detection too:** on laz, Docling 2.118 / transformers 5.10.1 on the CPU found 4, 3,
  3, 9, 6 and 4 (and could not run on MPS at all, the float64 error).

The laz files are under `~/.re-gate/docling-timing-2026-11-11/` on laz.

## 3. What there is today

### 3.1 Devices, chosen in three places, three ways

| Where | Default | Order | Notes |
|---|---|---|---|
| RE embeddings (`resource_explorer/embeddings.py`) | `auto` (`EmbeddingsConfig.device`) | MPS, CUDA, CPU | MPS before CUDA; no reason recorded |
| EA embeddings (`advisor/embeddings.py`) | `auto` (`advisor.yaml`'s `embeddings.device`) | CUDA/ROCm, MPS, CPU | logs the GPU's name; falls back to CPU on failure. `EmbeddingConfig`'s own default is `cpu`, used only if the file leaves it out; `EMBEDDING_DEVICE` is defined but not read on this path |
| Docling (`trellis_artifact_tree.adapters_pdf.keep_off_mps`, RE's `build_pdf_converter`) | Docling's `AUTO` | Docling's own | CPU instead of MPS only when transformers is older than 5.22.2, or `TRELLIS_DOCLING_ALLOW_MPS` says so |

The same machine can therefore embed on one device and convert PDFs on another, and nothing records which.

### 3.2 Installs: the lock's NVIDIA build on bare-metal Linux; a post-sync swap in the images

`uv.lock` resolves `torch` from PyPI. On Linux that wheel is the CUDA 13 build, and it brings `triton` and 15
`nvidia-*` packages. In hedwig's shared environment that is 2.7 GB of NVIDIA libraries, 1.1 GB of torch and
690 MB of triton; on a machine without an NVIDIA GPU, all of it is unused.

**The Docker images already avoid it** (`docker/Dockerfile.resource-explorer` lines 14–25 and 78ff,
`docker/Dockerfile.egeria-advisor` 63–77): after the last `uv sync` in the builder stage they uninstall torch,
torchvision, triton and the `nvidia-*` packages and reinstall torch from PyTorch's CPU-only index
(`docs/packaging.md` has the measured size delta). Their comments record why it must be the last step: a later
frozen `uv sync` sees the venv no longer matches the lock and silently reinstalls the CUDA build. That same
behaviour is why a machine on a non-default build (§7, hedwig) needs the build in the lock, not a swap after it.
EA's `docs/design/RUNTIME_AND_HARDWARE.md` §4a has the measurement behind the images' CPU-only choice.
(EA also lists `onnxruntime-gpu`, which has no macOS arm64 wheel; that is why `--all-extras` fails on a Mac,
CLAUDE.md "uv".)

The ROCm build for AMD is larger again: the wheel is 6.2 GB and installs to about 16 GB, so it should never be a
default.

## 4. Proposal A: one device choice at run time

A small shared function, for example `trellis_accel.pick_device(purpose)`, returning the device and the reason:

1. **Override:** `TRELLIS_DEVICE` = `cpu` | `cuda` | `mps` | `auto` (default `auto`). An override the installed
   PyTorch cannot honour is refused with a sentence, never quietly replaced. `rocm` is accepted as a name for
   `cuda`, since PyTorch reports AMD GPUs through the CUDA API.
2. **NVIDIA or AMD:** `torch.cuda.is_available()`. `torch.version.hip` says which one, for the record.
3. **Apple:** `torch.backends.mps.is_available()`, unless a known-bad combination applies. Today that is one rule,
   moved from `keep_off_mps`: transformers older than 5.22.2 runs Docling's layout model on the CPU. The rules live
   in one place, each with the incident that justified it.
4. **Otherwise the CPU.**

Every consumer asks it: RE's and EA's embeddings, and Docling (as `AcceleratorOptions(device=...)`). The choice and
its reason go in the log at start-up and in each run's record (a rebuild's progress, a survey's activity entry), so
a slow or failed run says what it ran on.

Settings stay per purpose, because they can differ: each purpose keeps its own setting (RE's
`EmbeddingsConfig.device`, EA's `advisor.yaml` `embeddings.device`, both `auto` today) and asks the shared function
only when it is `auto`. (An earlier version of this document said EA's embeddings default to `cpu`; that was
wrong, found while building Proposal A: EA reads `advisor.yaml`, which ships `auto`.)

The ONNX embedding path in EA (`onnxruntime` / `onnxruntime-gpu`) is a separate stack; it is out of scope here
and noted in §7.

## 5. Proposal B: install the PyTorch build each machine can use

Optional extras in the workspace, at most one per environment, each pointing `torch` (and `torchvision`) at its
own index:

```toml
# root pyproject.toml (sketch)
[project.optional-dependencies]
cpu  = ["torch", "torchvision"]
cuda = ["torch", "torchvision"]
rocm = ["torch", "torchvision"]

[tool.uv]
conflicts = [[{ extra = "cpu" }, { extra = "cuda" }, { extra = "rocm" }]]

[tool.uv.sources]
torch = [
  { index = "pytorch-cpu",  extra = "cpu",  marker = "sys_platform == 'linux'" },
  { index = "pytorch-cuda", extra = "cuda", marker = "sys_platform == 'linux'" },
  { index = "pytorch-rocm", extra = "rocm", marker = "sys_platform == 'linux'" },
]
# torchvision the same

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
# pytorch-cuda: .../whl/cu130; pytorch-rocm: .../whl/rocm7.2, the same way
```

- **macOS:** no extra; PyPI's build already includes MPS.
- **Linux images and CI:** `cpu`. For the images this is no size change (they already swap to the CPU build); it
  replaces the post-sync uninstall/reinstall with the lock itself, which a later `uv sync` cannot undo. The saving
  is on bare-metal Linux without an NVIDIA GPU: about 4.5 GB per environment.
- **A Linux machine with an NVIDIA GPU:** `cuda`. **With an AMD GPU (hedwig):** `rocm`.
- **The lock** records all three, and only the chosen one is downloaded.

Two things to settle in a spike before relying on this, because they are where uv's behaviour decides the design:

1. **A bare `uv sync` from the root** (CLAUDE.md, "uv") must keep giving every session a working environment. With
   no extra chosen, `torch` falls back to PyPI's (CUDA) build, which is today's behaviour. Whether to make `cpu` the
   default on Linux instead, and how (uv has default groups, not default extras), is the open question.
2. **The ROCm download is large and the network can drop it.** On hedwig a 6.2 GB download reset after 1.2 GB.
   Pre-fetching the wheel with a resumable download, then installing from the local file, worked; the install
   step should support that, and its temporary files must not go to a small RAM-backed `/tmp` (16 GB unpacked).

Choosing a build per machine needs a probe that runs before any PyTorch is installed: `nvidia-smi`, `/dev/kfd`
plus the GPU's `gfx_target_version` in `/sys/class/kfd`, or `uname -s` = Darwin. That can be a small script that
prints the extra to use; it chooses nothing on its own.

## 5a. A hardware check at start-up

Each app checks, at start-up, what the machine has against what the installed PyTorch can use, and says so. It
reports; it never installs, because a build is part of an environment every session on the machine shares, and
a multi-gigabyte change to it is the owner's call.

- **The machine:** the same probe as §5 (an NVIDIA GPU by `nvidia-smi` or `/proc/driver/nvidia`, an AMD GPU by
  `/dev/kfd` and `gfx_target_version`, Apple by the platform), with no PyTorch import needed.
- **The build:** `torch.version.cuda`, `torch.version.hip`, and whether CUDA or MPS is available.
- **What it says:** one line in the start-up log, the same in `/health`'s detail and the admin panel. A GPU the
  build cannot use is a warning that names the fix, for example "AMD Radeon 890M found; this environment has the
  CPU-only PyTorch; `uv sync --extra rocm` would make PDF conversion about 2x faster". A build the machine cannot
  use (the CUDA build with no NVIDIA GPU) is a note naming the smaller build.
- **The device chosen** (§4) is reported next to it, so "GPU found, GPU used" is one line.

## 6. Rollout

1. **Measure laz:** run the same timing script on Apple's GPU and on its CPU, for the MPS column in §2.
2. **Proposal A** (runtime) and the start-up check (§5a): the shared function and its tests; switch Docling and
   RE's embeddings to it; record the device in each run. Behaviour on today's machines is unchanged, except that
   the choice is now said. EA's embeddings are already on `auto`; the measurement in §7 decides whether queries
   should move to `cpu`.
3. **Spike for Proposal B:** a branch with the extras, `uv lock`, and builds on Linux CPU, on hedwig with `rocm`,
   and on a Mac with no extra; both images built with `cpu` and the post-sync swap removed, with their size
   checked against today's (it should not change); and how a machine keeps its extra across a plain `uv sync`
   (§7, hedwig).
4. **Proposal B** (install): the extras, the probe script, the images on `cpu` without the swap, and the
   documentation (CLAUDE.md "uv", `docs/packaging.md`).

## 7. Decisions

The project owner answered §7's four questions in the Claude Code session that wrote this proposal, on
2026-11-11, after reading its first version. Their words, verbatim, are quoted in each callout below; what
follows each quotation is this document's own wording of the decision and its conditions.

**Decision (project owner, 2026-11-11):** "yes, I think so - but we should build in a hardware check on trellis
startup?" The CPU-only build is the Linux default, with a hardware check at start-up (§5a).

**Decision (project owner, 2026-11-11):** "yes" (to question 2). Hedwig moves to `rocm`. Condition: a plain `uv sync` from the
root must not put hedwig back on the CPU build once CPU is the default (CLAUDE.md tells every session to run
exactly that). The spike (§6, step 3) settles how a machine keeps its extra across every sync: a uv setting if
there is one, else a wrapper that every session uses. Until then hedwig stays as it is; the start-up check is the
backstop that would notice a flip.

**Decision (project owner, 2026-11-11):** "Yes" (to question 3). Question 3 rested on this document's wrong premise that
EA's embeddings default to `cpu`: they already run on `auto` (`advisor.yaml`). The answer stands as `auto` for EA's
embeddings, which is what EA does today. The advice given with it still applies, now as a question of whether to move
queries back to `cpu`: EA embeds one short query at a time with a small model, where moving data to a GPU
can cost as much as it saves, and on hedwig the GPU is shared with Docling (and an LLM, if Ollama uses it). Measure
query-time and batch indexing on CPU and GPU first; `auto` for batch indexing and `cpu` for queries is a likely
outcome.

**Decision (project owner, 2026-11-11):** "Keep" (to question 4). EA's ONNX path (`onnxruntime-gpu`, NVIDIA only) stays, as an
extra; `--all-extras` still fails on macOS because of it (CLAUDE.md, "uv").
