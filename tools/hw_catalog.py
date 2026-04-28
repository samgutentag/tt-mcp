"""Static catalog of Tenstorrent hardware specs.

Looked up by the operator-declared `TT_HARDWARE` env var. Exact-match only;
fuzzy matching is a nice-to-have we deliberately skip, an operator who
typos "Tenstorrent n3000" should see the unknown-fallback shape rather
than silently pick "n300".

Why hardcoded? Two reasons. First, a live spec lookup over the network
adds a hop and a failure mode for information that almost never changes,
the silicon doesn't move under us. Second, the catalog is *part* of the
tool's value, the agent gets a real picture of the box in one call rather
than three.

Source-of-truth policy: every numeric field is either confirmed against
tenstorrent.com/hardware or a linked spec sheet, or marked `None` with a
`# TODO verify` comment. A wrong TFLOPS number is worse than no TFLOPS
number, an agent will reason from it. When in doubt, leave it None and
let `hardware_info` say so.

Each entry's `source` field links to the page where the confirmed values
were read, so a future maintainer can re-verify in one click.
"""

from __future__ import annotations

from typing import Any


# Keyed by the exact string an operator sets in `TT_HARDWARE`. Recommended
# values are the product names as they appear on tenstorrent.com/hardware,
# with the manufacturer prefix to keep the namespace open for non-TT
# accelerators we may catalog later.
HARDWARE_SPECS: dict[str, dict[str, Any]] = {
    "Tenstorrent n150": {
        "chip_family": "Wormhole",
        "chip_count": 1,
        "tensix_cores": 72,  # 72 active per Wormhole n150 product page
        "dram_gb": 12,
        "dram_type": "GDDR6",
        "dram_bandwidth_gbps": 288,
        "peak_bf16_tflops": None,  # TODO verify, not on consumer page
        "tdp_watts": 160,
        "form_factor": "PCIe Gen4 dual-slot card",
        "interconnect": None,  # TODO verify, single-card SKU
        "source": "https://tenstorrent.com/hardware/wormhole",
    },
    "Tenstorrent n300": {
        "chip_family": "Wormhole",
        "chip_count": 2,
        "tensix_cores": 128,  # 64 per chip x 2 active per n300 spec
        "dram_gb": 24,  # 12 GB per chip x 2 chips on the board
        "dram_type": "GDDR6",
        "dram_bandwidth_gbps": 576,  # 288 per chip x 2
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": 300,
        "form_factor": "PCIe Gen4 dual-slot card",
        "interconnect": "On-board chip-to-chip",
        "source": "https://tenstorrent.com/hardware/wormhole",
    },
    "Tenstorrent p100": {
        "chip_family": "Blackhole",
        "chip_count": 1,
        "tensix_cores": None,  # TODO verify, Blackhole has different layout from Wormhole
        "dram_gb": 28,
        "dram_type": "GDDR6",
        "dram_bandwidth_gbps": None,  # TODO verify
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": 300,
        "form_factor": "PCIe Gen5 dual-slot card",
        "interconnect": None,
        "source": "https://tenstorrent.com/hardware/blackhole",
    },
    "Tenstorrent p150": {
        "chip_family": "Blackhole",
        "chip_count": 1,
        "tensix_cores": None,  # TODO verify
        "dram_gb": 32,
        "dram_type": "GDDR6",
        "dram_bandwidth_gbps": None,  # TODO verify
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": 300,
        "form_factor": "PCIe Gen5 dual-slot card",
        "interconnect": None,
        "source": "https://tenstorrent.com/hardware/blackhole",
    },
    "Tenstorrent Wormhole LoudBox": {
        "chip_family": "Wormhole",
        "chip_count": 8,  # 4 x n300 cards, 2 chips per card
        "dram_gb": 96,  # 8 chips x 12 GB
        "dram_type": "GDDR6",
        "tensix_cores": None,  # TODO verify total active count
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": None,  # TODO verify (system-level)
        "form_factor": "4U workstation",
        "interconnect": "Mesh across 4 cards via on-card chip-to-chip",
        "source": "https://tenstorrent.com/hardware/wormhole",
    },
    "Tenstorrent Wormhole QuietBox": {
        "chip_family": "Wormhole",
        "chip_count": 8,
        "dram_gb": 96,
        "dram_type": "GDDR6",
        "tensix_cores": None,  # TODO verify
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": None,  # TODO verify
        "form_factor": "Workstation, low-noise variant of LoudBox",
        "interconnect": "Mesh across 4 cards via on-card chip-to-chip",
        "source": "https://tenstorrent.com/hardware/wormhole",
    },
    "Tenstorrent Blackhole LoudBox": {
        "chip_family": "Blackhole",
        "chip_count": None,  # TODO verify card configuration
        "dram_gb": None,  # TODO verify
        "dram_type": "GDDR6",
        "tensix_cores": None,  # TODO verify
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": None,  # TODO verify
        "form_factor": "4U workstation",
        "interconnect": None,  # TODO verify
        "source": "https://tenstorrent.com/hardware/blackhole",
    },
    "Tenstorrent Blackhole QuietBox": {
        "chip_family": "Blackhole",
        "chip_count": None,  # TODO verify
        "dram_gb": None,  # TODO verify
        "dram_type": "GDDR6",
        "tensix_cores": None,  # TODO verify
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": None,  # TODO verify
        "form_factor": "Workstation, low-noise variant",
        "interconnect": None,  # TODO verify
        "source": "https://tenstorrent.com/hardware/blackhole",
    },
    "Tenstorrent Wormhole Galaxy": {
        "chip_family": "Wormhole",
        "chip_count": 32,  # 32-chip mesh advertised on the Galaxy product page
        "dram_gb": 384,  # 32 x 12 GB
        "dram_type": "GDDR6",
        "tensix_cores": None,  # TODO verify total active count
        "peak_bf16_tflops": None,  # TODO verify
        "tdp_watts": None,  # TODO verify (rack-level)
        "form_factor": "Rack server, 6U",
        "interconnect": "32-chip 2D torus mesh, on-board",
        "source": "https://tenstorrent.com/hardware/galaxy",
    },
}


def lookup(label: str | None) -> dict[str, Any]:
    """Look up a hardware label in the catalog.

    Exact match only. Misses return `{"unknown": True, "label": <value>}`
    rather than raising, so an unrecognised box still gets a useful
    `hardware_info` payload, the agent learns the operator's declared
    label even if we don't know the silicon. `None` (no `TT_HARDWARE`
    set) gets the same treatment.

    Args:
        label: The exact string the operator put in `TT_HARDWARE`, or
            `None` if the env var was unset.

    Returns:
        A dict matching the `HARDWARE_SPECS` schema for known entries,
        or `{"unknown": True, "label": label}` for misses.
    """
    if label is None:
        return {"unknown": True, "label": None}
    if label in HARDWARE_SPECS:
        return HARDWARE_SPECS[label]
    return {"unknown": True, "label": label}


if __name__ == "__main__":
    # Self-test: confirm every entry has the expected keys and that lookup
    # round-trips. Run with: python -m tools.hw_catalog
    expected_keys = {
        "chip_family",
        "chip_count",
        "tensix_cores",
        "dram_gb",
        "dram_type",
        "peak_bf16_tflops",
        "form_factor",
        "interconnect",
        "source",
    }
    for label, spec in HARDWARE_SPECS.items():
        missing = expected_keys - spec.keys()
        assert not missing, f"{label} missing keys: {missing}"
        assert spec["source"].startswith("https://"), f"{label} source must be a URL"

    assert lookup("Tenstorrent n300")["chip_family"] == "Wormhole"
    assert lookup("nonsense") == {"unknown": True, "label": "nonsense"}
    assert lookup(None) == {"unknown": True, "label": None}

    # Report TODO coverage so a maintainer can see at a glance what still
    # needs verification.
    todo_count = sum(
        1 for spec in HARDWARE_SPECS.values()
        for v in spec.values()
        if v is None
    )
    print(f"ok: catalog self-test passed ({len(HARDWARE_SPECS)} entries, {todo_count} TODO fields)")
