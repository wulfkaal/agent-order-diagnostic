#!/usr/bin/env python3
"""Write deterministic synthetic calibration fixtures."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import order_diagnostic as diagnostic


DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "fixtures")
MODEL_KEYS = [
    hashlib.sha256(f"hp-order-fixture/model/{number}".encode()).hexdigest()
    for number in (0, 1)
]
ARMS = [
    (model, configuration)
    for model in MODEL_KEYS
    for configuration in ("a" * 64, "b" * 64)
]
ARM_OFFSET = {0: 0.00, 1: 0.05, 2: -0.05, 3: 0.10}


def uniform(case, *parts):
    # This hash domain string is fixed. Changing it changes every fixture.
    digest = hashlib.sha256(
        b"hanria/hp-order-fix/v1\0"
        + case.encode()
        + b"\0"
        + b"\0".join(parts)
    ).digest()
    return int.from_bytes(digest, "big") / 2**256


def treatment_id(case, model, configuration):
    digest = hashlib.sha256(f"{case}/{model}/{configuration}".encode()).hexdigest()
    return "hp-arm:" + digest


def build(
    case,
    block_size,
    n_instances=40,
    plant=None,
    lift=0.35,
    unopened=0,
    excluded=0,
    cutoff_suffix=0,
    alternate_machines=False,
    retry_slot=None,
    assigned_unopened=None,
):
    design_digest = hashlib.sha256(f"hp-order-fixture/{case}".encode()).hexdigest()
    beacon = hashlib.sha256(
        f"hp-order-fixture-beacon/{case}".encode()
    ).hexdigest()
    slots = []
    for index in range(n_instances):
        instance = f"inst-{index:03d}"
        for arm, (model, configuration) in enumerate(ARMS):
            slots.append(
                {
                    "instance": instance,
                    "treatment_id": treatment_id(case, model, configuration),
                    "replicate": 1,
                    "model_key": model,
                    "hp_digest": configuration,
                    "opened": True,
                    "outcome": None,
                    "_arm": arm,
                }
            )

    order = diagnostic.ordinals(design_digest, bytes.fromhex(beacon), slots)
    slots.sort(key=lambda slot: order[diagnostic.perm_key(slot)])

    for position, slot in enumerate(slots):
        if unopened and position % 17 == 5 and unopened > 0:
            slot["opened"] = False
            unopened -= 1
        elif excluded and position % 13 == 7 and excluded > 0:
            excluded -= 1
            slot["_excluded"] = True

    for position in range(len(slots) - cutoff_suffix, len(slots)):
        slots[position]["opened"] = False

    for position, slot in enumerate(slots):
        if slot["opened"]:
            machine = (
                "machine-2"
                if alternate_machines and position % 2
                else "machine-1"
            )
            slot["nodes"] = [machine]
            slot["attempts"] = 2 if position == retry_slot else 1
        else:
            slot["nodes"] = []
            slot["attempts"] = 0

    if assigned_unopened is not None:
        slot = slots[assigned_unopened]
        slot["opened"] = False
        slot["nodes"] = []
        slot["attempts"] = 1

    for position, slot in enumerate(slots):
        if not slot["opened"] or slot.get("_excluded"):
            continue
        block = position // block_size + 1
        previous = (
            [
                prior
                for prior in slots[
                    (block - 2) * block_size : (block - 1) * block_size
                ]
                if prior["opened"]
            ]
            if block >= 2
            else []
        )
        probability = (
            0.30
            + 0.40 * uniform(case, slot["instance"].encode())
            + ARM_OFFSET[slot["_arm"]]
        )
        if plant and any(
            diagnostic.attr(prior, plant) == diagnostic.attr(slot, plant)
            for prior in previous
        ):
            probability += lift
        slot["outcome"] = (
            1
            if uniform(case, diagnostic.perm_key(slot), b"y")
            < min(probability, 0.97)
            else 0
        )

    for slot in slots:
        slot.pop("_arm")
        slot.pop("_excluded", None)
        slot.pop("opened")
    slots.sort(
        key=lambda slot: (
            slot["instance"],
            slot["treatment_id"],
            slot["replicate"],
        )
    )
    return {
        "design_digest": design_digest,
        "beacon": beacon,
        "concurrency": block_size,
        "slots": slots,
    }


CASES = {
    "01-null-w1": dict(block_size=1),
    "02-model-carryover-w1": dict(block_size=1, plant="model"),
    "03-hp-carryover-w1": dict(block_size=1, plant="hp"),
    "04-insufficient-w8": dict(block_size=8, plant="model"),
    "05-unopened-excluded-w2": dict(block_size=2, unopened=6, excluded=6),
    "06-cutoff-suffix-w1": dict(
        block_size=1, plant="model", cutoff_suffix=10
    ),
    "07-multi-node-w1": dict(
        block_size=1, plant="model", alternate_machines=True
    ),
    "08-retry-present-w1": dict(
        block_size=1, plant="model", retry_slot=40
    ),
    "09-assigned-unopened-w1": dict(
        block_size=1, plant="model", assigned_unopened=159
    ),
}


def write_fixtures(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    manifest_lines = []
    for name, arguments in CASES.items():
        inp = build(name, **arguments)
        expected = diagnostic.diagnose(inp)
        for suffix, obj in (("input", inp), ("expected", expected)):
            filename = f"{name}.{suffix}.json"
            path = os.path.join(output_dir, filename)
            data = (diagnostic.canonical(obj) + "\n").encode()
            with open(path, "wb") as fixture_file:
                fixture_file.write(data)
            digest = hashlib.sha256(data).hexdigest()
            manifest_lines.append(f"{digest}  {filename}")
        print(name, diagnostic.canonical(expected["channels"]))

    manifest = ("\n".join(manifest_lines) + "\n").encode()
    with open(os.path.join(output_dir, "MANIFEST.sha256"), "wb") as manifest_file:
        manifest_file.write(manifest)


if __name__ == "__main__":
    write_fixtures(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT)
