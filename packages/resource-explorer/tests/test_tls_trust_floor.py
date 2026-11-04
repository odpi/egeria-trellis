"""The CA bundle RE verifies TLS with no longer trusts emSign ECC Root CA - G3 (October 2026 emSign G3 key compromise)."""
import certifi

DISTRUSTED_ROOT = "emSign ECC Root CA - G3"
FLOOR = (2026, 10, 18)


def test_certifi_is_at_or_above_the_release_that_drops_the_emsign_g3_root():
    version = tuple(int(part) for part in certifi.__version__.split(".")[:3])
    assert version >= FLOOR, f"certifi {certifi.__version__} predates {'.'.join(map(str, FLOOR))}"


def test_the_installed_bundle_does_not_hold_the_distrusted_root():
    with open(certifi.where(), encoding="utf-8") as bundle:
        assert DISTRUSTED_ROOT not in bundle.read()
