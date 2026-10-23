#!/usr/bin/env python3
"""Reference implementation of the hp-order/v2 carryover diagnostic.

The program uses only the Python standard library. It represents all outcome
arithmetic exactly with fractions.Fraction.

Usage:
  order_diagnostic.py selftest
  order_diagnostic.py run <input.json>
"""
import hashlib
import json
import sys
from fractions import Fraction


NULL_DRAWS = 999
MIN_PER_GROUP = 20
FLAG_MAX_EXTREME = 4
FLAG_MIN_ABS_MILLI = 50
CHANNELS = ("model", "hp")


def sha_hex(*parts: bytes) -> str:
    return hashlib.sha256(b"".join(parts)).hexdigest()


def u32be(n: int) -> bytes:
    return n.to_bytes(4, "big")


def perm_key(slot) -> bytes:
    instance = slot["instance"].encode()
    treatment = slot["treatment_id"].encode()
    return (
        u32be(len(instance))
        + instance
        + u32be(len(treatment))
        + treatment
        + u32be(slot["replicate"])
    )


def ordinals(design_digest: str, beacon: bytes, slots):
    """Return the one-based randomized ordinal for each slot key."""
    # These hash domain strings are fixed identifiers. Changing them changes every reported number.
    seed = sha_hex(b"hanria/hp-seed/v2\0", design_digest.encode(), beacon)
    ranked = []
    for slot in slots:
        key = perm_key(slot)
        rank = hashlib.sha256(
            b"hanria/hp-perm/v1\0" + seed.encode() + key
        ).digest()
        ranked.append((rank, key))
    ranked.sort()
    return {key: index + 1 for index, (_, key) in enumerate(ranked)}


def null_beacon(design_digest: str, draw: int) -> bytes:
    return hashlib.sha256(
        b"hanria/hp-order-null/v2\0" + design_digest.encode() + u32be(draw)
    ).digest()


def attr(slot, channel):
    return slot["model_key"] if channel == "model" else slot["hp_digest"]


def contrast(slots, order, block_size, channel):
    """Return the pooled contrast and its contributing counts."""
    attrs_in_block = {}
    for slot in slots:
        block = (order[perm_key(slot)] - 1) // block_size + 1
        attrs_in_block.setdefault(block, set()).add(attr(slot, channel))

    cells = {}
    for slot in slots:
        block = (order[perm_key(slot)] - 1) // block_size + 1
        if block < 2:
            continue
        warm = attr(slot, channel) in attrs_in_block[block - 1]
        cell = cells.setdefault(slot["treatment_id"], [0, 0, 0, 0])
        if warm:
            cell[0] += 1
            cell[1] += slot["outcome"]
        else:
            cell[2] += 1
            cell[3] += slot["outcome"]

    numerator = Fraction(0)
    denominator = Fraction(0)
    n_warm = 0
    n_cold = 0
    contributing = 0
    for warm_n, warm_x, cold_n, cold_x in cells.values():
        if warm_n == 0 or cold_n == 0:
            continue
        weight = Fraction(warm_n * cold_n, warm_n + cold_n)
        numerator += weight * (
            Fraction(warm_x, warm_n) - Fraction(cold_x, cold_n)
        )
        denominator += weight
        n_warm += warm_n
        n_cold += cold_n
        contributing += 1

    delta = None if denominator == 0 else numerator / denominator
    return delta, n_warm, n_cold, contributing


def round_milli(value: Fraction) -> int:
    """Round 1000 times the value halfway away from zero."""
    scaled = value * 1000
    sign = -1 if scaled < 0 else 1
    absolute = abs(scaled)
    quotient, remainder = divmod(absolute.numerator, absolute.denominator)
    if 2 * remainder >= absolute.denominator:
        quotient += 1
    return sign * quotient


def diagnose(inp):
    design_digest = inp["design_digest"]
    block_size = inp["concurrency"]
    beacon = bytes.fromhex(inp["beacon"])
    slots = inp["slots"]

    for slot in slots:
        assert slot["nodes"] == sorted(set(slot["nodes"])), (
            "nodes must be sorted and unique"
        )
        assert isinstance(slot["attempts"], int) and 0 <= slot["attempts"] <= 3, (
            "attempts in 0..3"
        )
        assert not slot["nodes"] or slot["attempts"] >= 1, (
            "a node implies an attempt"
        )
        assert slot["outcome"] is None or (
            slot["attempts"] >= 1 and slot["nodes"]
        ), "an outcome implies an opened attempt"

    out = {
        "design_digest": design_digest,
        "version": "hp-order/v2",
        "null_draws": NULL_DRAWS,
        "channels": [],
    }

    if any(slot["outcome"] is None for slot in slots):
        design_reason = "incomplete"
    elif any(slot["attempts"] > 1 for slot in slots):
        design_reason = "retries_present"
    elif len({node for slot in slots for node in slot["nodes"]}) != 1:
        design_reason = "multi_node"
    else:
        design_reason = None

    if design_reason is None:
        order = ordinals(design_digest, beacon, slots)
        observed = {
            channel: contrast(slots, order, block_size, channel)
            for channel in CHANNELS
        }
    else:
        observed = {channel: (None, 0, 0, 0) for channel in CHANNELS}

    reason = {}
    for channel in CHANNELS:
        if design_reason:
            reason[channel] = design_reason
        elif (
            observed[channel][0] is None
            or observed[channel][1] < MIN_PER_GROUP
            or observed[channel][2] < MIN_PER_GROUP
        ):
            reason[channel] = "too_few_units"
        else:
            reason[channel] = None

    sufficient = {channel: reason[channel] is None for channel in CHANNELS}
    extreme = {channel: 0 for channel in CHANNELS}
    if any(sufficient.values()):
        for draw in range(NULL_DRAWS):
            order = ordinals(
                design_digest, null_beacon(design_digest, draw), slots
            )
            for channel in CHANNELS:
                if not sufficient[channel]:
                    continue
                delta = contrast(slots, order, block_size, channel)[0]
                if delta is None or abs(delta) >= abs(observed[channel][0]):
                    extreme[channel] += 1

    for channel in CHANNELS:
        delta, n_warm, n_cold, n_cells = observed[channel]
        row = {
            "channel": channel,
            "n_warm": n_warm,
            "n_cold": n_cold,
            "contributing_cells": n_cells,
            "reason": reason[channel],
        }
        if not sufficient[channel]:
            row.update(
                state="insufficient",
                delta_num=None,
                delta_den=None,
                delta_milli=None,
                null_extreme_count=None,
                flagged=False,
            )
        else:
            rounded = round_milli(delta)
            row.update(
                state="computed",
                delta_num=str(delta.numerator),
                delta_den=str(delta.denominator),
                delta_milli=rounded,
                null_extreme_count=extreme[channel],
                flagged=(
                    extreme[channel] <= FLAG_MAX_EXTREME
                    and abs(rounded) >= FLAG_MIN_ABS_MILLI
                ),
            )
        out["channels"].append(row)
    return out


def canonical(obj) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def selftest():
    slots = [
        {"instance": f"i{i}", "treatment_id": f"a{arm}", "replicate": replicate}
        for i in range(3)
        for arm in range(2)
        for replicate in (1, 2)
    ]
    order = ordinals("d" * 64, bytes([7]) * 32, slots)
    got = [
        f"{slot['instance']}:{slot['treatment_id']}:{slot['replicate']}"
        for slot in sorted(slots, key=lambda item: order[perm_key(item)])
    ]
    want = [
        "i2:a0:1",
        "i0:a0:1",
        "i1:a0:1",
        "i1:a0:2",
        "i0:a1:1",
        "i1:a1:2",
        "i0:a1:2",
        "i2:a1:2",
        "i1:a1:1",
        "i2:a1:1",
        "i2:a0:2",
        "i0:a0:2",
    ]
    assert got == want, got

    first_null = null_beacon("d" * 64, 0).hex()
    last_null = null_beacon("d" * 64, 998).hex()
    assert first_null == (
        "8021f7292b6393bc1e80998ed03dae558a20393a5d6cc1dce04b690d5fadcaf0"
    )
    assert last_null == (
        "5267408b9ff1eff87300012c6338c67df119e1913ea9e0461693d1506df2da8d"
    )
    print("ordering test vector: OK")
    print("null beacon d*64, draw=0:", first_null)
    print("null beacon d*64, draw=998:", last_null)


if __name__ == "__main__":
    if sys.argv[1] == "selftest":
        selftest()
    else:
        with open(sys.argv[2], encoding="utf-8") as input_file:
            print(canonical(diagnose(json.load(input_file))))
