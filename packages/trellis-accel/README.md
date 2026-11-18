# trellis-accel

One device choice for every model Trellis runs on PyTorch (Docling's PDF models, sentence-transformers
embeddings), and a start-up report of the machine's GPUs against what the installed PyTorch can use.
See `docs/hardware-acceleration-proposal.md`.

    from trellis_accel import pick_device, hardware_report
    choice = pick_device(cfg.device, purpose="embeddings")   # Choice(device="cuda", reason="...")
    report = hardware_report()                                # machine, build, choice, findings

`TRELLIS_DEVICE` (`cpu` | `cuda` | `rocm` | `mps` | `auto`) overrides `auto` everywhere; an app's own explicit
setting (e.g. `EMBEDDING_DEVICE=cpu`) wins over it. A device the installed PyTorch cannot use is refused with a
sentence, never quietly replaced. The report never installs anything.
