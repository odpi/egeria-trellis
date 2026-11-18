"""trellis-accel: one device choice and the start-up report (docs/hardware-acceleration-proposal.md).

PyTorch is replaced by a fake in each test, so the choice is tested for NVIDIA, AMD, Apple and CPU-only machines on
any machine; the OS probe is pointed at fake /sys and /proc trees."""
from __future__ import annotations

import types

import pytest
import trellis_accel as ta


def _torch(*, cuda=False, mps=False, hip=None, cuda_build="13.0", name="Fake GPU"):
    t = types.SimpleNamespace()
    t.__version__ = "2.13.0" + ("+rocm7.2" if hip else "")
    t.version = types.SimpleNamespace(cuda=None if hip else cuda_build, hip=hip)
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda, get_device_name=lambda i=0: name)
    t.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    return t


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for k in (ta.DEVICE_ENV, ta.ALLOW_MPS_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ta, "KFD_NODES", tmp_path / "no-kfd")
    monkeypatch.setattr(ta, "NVIDIA_PROC", tmp_path / "no-nvidia")
    monkeypatch.setattr(ta.shutil, "which", lambda name: None)
    ta.report_once.cache_clear()


def _use(monkeypatch, torch):
    monkeypatch.setattr(ta, "_torch", lambda: torch)


# ── the choice ──

def test_an_amd_gpu_is_chosen_and_named(monkeypatch):
    _use(monkeypatch, _torch(cuda=True, hip="7.2.53211", name="AMD Radeon 890M Graphics"))
    c = ta.pick_device()
    assert c.device == "cuda" and c.gpu == "AMD Radeon 890M Graphics"
    assert "AMD (ROCm 7.2.53211)" in c.reason


def test_an_nvidia_gpu_is_chosen_before_apples(monkeypatch):
    _use(monkeypatch, _torch(cuda=True, mps=True, name="RTX"))
    c = ta.pick_device()
    assert c.device == "cuda" and "NVIDIA (CUDA)" in c.reason


def test_apples_gpu_when_there_is_no_other(monkeypatch):
    _use(monkeypatch, _torch(mps=True))
    assert ta.pick_device(purpose="embeddings").device == "mps"


def test_cpu_when_there_is_no_gpu_or_no_torch(monkeypatch):
    _use(monkeypatch, _torch())
    assert ta.pick_device().device == "cpu"
    _use(monkeypatch, None)
    c = ta.pick_device()
    assert c.device == "cpu" and c.reason == "PyTorch is not installed"


def test_docling_stays_off_apples_gpu_with_an_old_transformers(monkeypatch):
    _use(monkeypatch, _torch(mps=True))
    monkeypatch.setattr(ta.metadata, "version", lambda n: "5.10.1")
    c = ta.pick_device(purpose="docling")
    assert c.device == "cpu" and "transformers 5.10.1 predates 5.22.2" in c.reason
    assert ta.pick_device(purpose="embeddings").device == "mps"     # the rule is Docling's only


def test_docling_uses_apples_gpu_with_a_fixed_transformers(monkeypatch):
    _use(monkeypatch, _torch(mps=True))
    monkeypatch.setattr(ta.metadata, "version", lambda n: "5.22.2")
    assert ta.pick_device(purpose="docling").device == "mps"


@pytest.mark.parametrize(("value", "device"), [("0", "cpu"), ("no", "cpu"), ("1", "mps"), ("true", "mps")])
def test_the_docling_mps_switch_still_decides(monkeypatch, value, device):
    _use(monkeypatch, _torch(mps=True))
    monkeypatch.setattr(ta.metadata, "version", lambda n: "5.10.1" if device == "mps" else "5.22.2")
    monkeypatch.setenv(ta.ALLOW_MPS_ENV, value)
    assert ta.pick_device(purpose="docling").device == device


# ── overrides ──

def test_trellis_device_overrides_auto(monkeypatch):
    _use(monkeypatch, _torch(cuda=True))
    monkeypatch.setenv(ta.DEVICE_ENV, "cpu")
    c = ta.pick_device()
    assert c.device == "cpu" and c.reason == "TRELLIS_DEVICE asks for cpu"


def test_an_apps_own_setting_wins_over_trellis_device(monkeypatch):
    _use(monkeypatch, _torch(cuda=True))
    monkeypatch.setenv(ta.DEVICE_ENV, "cuda")
    assert ta.pick_device("cpu", purpose="embeddings").reason == "this app's setting asks for cpu"


def test_rocm_is_a_name_for_cuda(monkeypatch):
    _use(monkeypatch, _torch(cuda=True, hip="7.2"))
    monkeypatch.setenv(ta.DEVICE_ENV, "rocm")
    assert ta.pick_device().device == "cuda"


def test_a_device_this_torch_cannot_use_is_refused_with_a_sentence(monkeypatch):
    _use(monkeypatch, _torch())
    with pytest.raises(ta.DeviceUnavailable, match="asks for cuda, which PyTorch 2.13.0 cannot use"):
        ta.pick_device("cuda")
    monkeypatch.setenv(ta.DEVICE_ENV, "tpu")
    with pytest.raises(ta.DeviceUnavailable, match="it can be cpu, cuda, rocm, mps or auto"):
        ta.pick_device()


def test_asking_for_mps_where_docling_must_not_use_it_is_refused(monkeypatch):
    _use(monkeypatch, _torch(mps=True))
    monkeypatch.setattr(ta.metadata, "version", lambda n: "5.10.1")
    with pytest.raises(ta.DeviceUnavailable, match="asks for mps, but transformers 5.10.1"):
        ta.pick_device("mps", purpose="docling")


# ── the machine and the report ──

def _kfd(tmp_path, *versions):
    for i, v in enumerate(versions):
        node = tmp_path / "kfd" / str(i)
        node.mkdir(parents=True)
        (node / "properties").write_text(f"cpu_cores_count 0\nsimd_count 32\ngfx_target_version {v}\n")
    return tmp_path / "kfd"


def test_the_amd_gpu_is_read_from_kfd_as_its_gfx_name(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "KFD_NODES", _kfd(tmp_path, 0, 110500, 90010))   # node 0 is the CPU
    amd = ta.probe_machine()["amd"]
    assert amd["status"] == "found" and amd["gpus"] == ["gfx1150", "gfx90a"]


def test_an_nvidia_gpu_is_read_from_its_driver(monkeypatch, tmp_path):
    gpu = tmp_path / "nv" / "0000:01:00.0"
    gpu.mkdir(parents=True)
    (gpu / "information").write_text("Model: \t\t NVIDIA GeForce RTX 4090\nIRQ: 1\n")
    monkeypatch.setattr(ta, "NVIDIA_PROC", tmp_path / "nv")
    assert ta.probe_machine()["nvidia"] == {"status": "found", "gpus": ["NVIDIA GeForce RTX 4090"], "reason": ""}


def test_no_driver_is_none_with_the_reason():
    m = ta.probe_machine()
    assert m["nvidia"]["status"] == "none" and "no NVIDIA driver" in m["nvidia"]["reason"]
    assert m["amd"]["status"] == "none" and "/sys/class/kfd" in m["amd"]["reason"]


def test_a_failing_nvidia_smi_is_not_checked_not_none(monkeypatch):
    """Architect C2: a failing nvidia-smi says nothing about whether there is a GPU."""
    monkeypatch.setattr(ta.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    monkeypatch.setattr(ta.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=9, stdout="", stderr="NVIDIA-SMI has failed because it couldn't communicate with the driver"))
    nv = ta.probe_machine()["nvidia"]
    assert nv["status"] == "not checked" and "exit 9" in nv["reason"] and "couldn't communicate" in nv["reason"]


def test_a_probe_that_raises_is_not_checked_and_the_report_still_comes(monkeypatch, tmp_path):
    """Architect C2: a kfd read error used to raise the whole report, and /health/hardware 500'd on every call."""
    kfd = _kfd(tmp_path, 110500)
    (kfd / "0" / "properties").chmod(0)
    monkeypatch.setattr(ta, "KFD_NODES", kfd)
    monkeypatch.setattr(ta.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied")))
    _use(monkeypatch, _torch())
    r = ta.hardware_report()
    assert r["machine"]["amd"]["status"] == "not checked" and "PermissionError" in r["machine"]["amd"]["reason"]
    assert any(f["text"].startswith("AMD GPUs not checked: PermissionError") for f in r["findings"])


def test_the_report_never_raises(monkeypatch):
    monkeypatch.setattr(ta, "probe_machine", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    r = ta.hardware_report()
    assert r["choices"] == {} and "could not be made: RuntimeError: boom" in r["findings"][0]["text"]


def test_hedwig_today_an_amd_gpu_and_the_cuda_build(monkeypatch, tmp_path):
    """hedwig on 2026-11-11: a Radeon 890M, and the lock's CUDA PyTorch, which cannot use it."""
    monkeypatch.setattr(ta, "KFD_NODES", _kfd(tmp_path, 0, 110500))
    _use(monkeypatch, _torch(cuda=False))
    r = ta.hardware_report({"embeddings": "auto", "docling": "auto"})
    assert {p: c["device"] for p, c in r["choices"].items()} == {"embeddings": "cpu", "docling": "cpu"}
    texts = [f["text"] for f in r["findings"]]
    assert any(t.startswith("AMD GPU gfx1150 found, but this environment has the CUDA PyTorch; a ROCm build")
               for t in texts)
    assert any("CUDA build (about 4.5 GB of NVIDIA libraries) and the machine has no NVIDIA GPU" in t for t in texts)
    line = ta.startup_line(r)
    assert line.startswith("hardware: PyTorch 2.13.0 (cuda); embeddings (setting auto): cpu, no GPU this PyTorch can "
                           "use; docling (setting auto): cpu")


def test_each_purpose_is_reported_with_its_real_setting(monkeypatch):
    """Architect C1: when an app's embeddings setting is "cpu" while Docling is "auto" on an Apple GPU, the
    report must say both, not one generic "auto" choice."""
    _use(monkeypatch, _torch(mps=True))
    monkeypatch.setattr(ta.metadata, "version", lambda n: "5.22.2")
    r = ta.hardware_report({"embeddings": "cpu", "docling": "auto"})
    assert r["choices"]["embeddings"] == {"setting": "cpu", "device": "cpu", "reason": "this app's setting asks for cpu",
                                          "gpu": None}
    assert r["choices"]["docling"]["device"] == "mps" and r["choices"]["docling"]["setting"] == "auto"
    assert "embeddings (setting cpu): cpu" in ta.startup_line(r) and "docling (setting auto): mps" in ta.startup_line(r)


def test_hedwig_with_rocm_says_nothing_but_what_it_uses(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "KFD_NODES", _kfd(tmp_path, 0, 110500))
    _use(monkeypatch, _torch(cuda=True, hip="7.2.53211", name="AMD Radeon 890M Graphics"))
    r = ta.hardware_report({"docling": "auto"})
    assert r["findings"] == [] and r["choices"]["docling"]["device"] == "cuda" and r["build"]["kind"] == "rocm"


def test_a_matching_gpu_that_pytorch_cannot_open_is_a_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "KFD_NODES", _kfd(tmp_path, 110500))
    _use(monkeypatch, _torch(cuda=False, hip="7.2"))
    assert any("cannot use the GPU (driver, or permission" in f["text"] for f in ta.hardware_report()["findings"])


def test_a_refused_setting_is_a_finding_not_a_crash(monkeypatch):
    _use(monkeypatch, _torch())
    r = ta.hardware_report({"embeddings": "cuda"})
    assert r["choices"]["embeddings"]["device"] is None
    assert r["findings"][-1]["level"] == "warn" and r["findings"][-1]["text"].startswith("embeddings: this app's setting")


def test_the_report_is_made_once_per_set_of_purposes(monkeypatch):
    calls = []
    monkeypatch.setattr(ta, "hardware_report", lambda purposes=None: calls.append(purposes) or {"x": 1})
    a = ta.report_once({"embeddings": "cpu", "docling": "auto"})
    assert ta.report_once({"docling": "auto", "embeddings": "cpu"}) is a       # same set, any order: one report
    ta.report_once({"embeddings": "auto"})
    assert calls == [{"docling": "auto", "embeddings": "cpu"}, {"embeddings": "auto"}]
