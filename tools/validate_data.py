#!/usr/bin/env python3
"""Validate LoongPerf measurement JSON files.

Checks both the JSON Schema and the semantic consistency of the data:
operand references, commands, instruction counts, latency derivations,
throughput CPI/IPC, and per-instruction documentation pages.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
SCHEMA_PATH = ROOT / "schema" / "instruction.schema.json"

EPS = 0.02

RETIRED_MULTIPLIER = {
    "xvsrlrn.h.w": 2,
    "xvsran.h.w": 2,
    "vsran.b.h": 2,
    "vsrarn.h.w": 2,
    "vsrlni.b.h": 2,
    "vsrarn.w.d": 2,
    "xvsrani.w.d": 2,
    "vsrlni.h.w": 2,
    "xvsrlrni.w.d": 2,
    "vsrarni.b.h": 2,
    "xvsrln.h.w": 2,
    "xvsrarn.h.w": 2,
    "xvsrarni.b.h": 2,
    "xvsrani.h.w": 2,
    "vsrlrni.b.h": 2,
    "vsrani.b.h": 2,
    "vsrln.w.d": 2,
    "xvsrln.w.d": 2,
    "xvsrlni.b.h": 2,
    "xvsran.w.d": 2,
    "xvsrarn.b.h": 2,
    "xvsrarni.h.w": 2,
    "vsrlrn.h.w": 2,
    "xvsrlni.w.d": 2,
    "vsrlrn.w.d": 2,
    "vsran.h.w": 2,
    "vsrln.h.w": 2,
    "xvsrarn.w.d": 2,
    "vsran.w.d": 2,
    "vsrani.w.d": 2,
    "vsrarni.w.d": 2,
    "xvsrlrn.w.d": 2,
    "vsrani.h.w": 2,
    "vsrlrni.w.d": 2,
    "vsrlrni.h.w": 2,
    "xvsran.b.h": 2,
    "xvsrln.b.h": 2,
    "vsrarni.h.w": 2,
    "vsrlrn.b.h": 2,
    "xvsrlrn.b.h": 2,
    "xvsrlrni.h.w": 2,
    "xvsrlrni.b.h": 2,
    "vsrln.b.h": 2,
    "vsrarn.b.h": 2,
    "xvsrlni.h.w": 2,
    "xvsrarni.w.d": 2,
    "vsrlni.w.d": 2,
    "xvsrani.b.h": 2,
    "xvreplve.d": 2,
    "xvreplve.w": 2,
    "xvreplve.h": 2,
    "xvreplve.b": 2,
    "xvsllwil.du.wu": 2,
    "xvsllwil.d.w": 2,
    "xvsllwil.wu.hu": 2,
    "xvsllwil.w.h": 2,
    "xvsllwil.hu.bu": 2,
    "xvsllwil.h.b": 2,
    "vsllwil.du.wu": 2,
    "vsllwil.d.w": 2,
    "vsllwil.wu.hu": 2,
    "vsllwil.w.h": 2,
    "vsllwil.hu.bu": 2,
    "vsllwil.h.b": 2,
    "xvinsgr2vr.d": 2,
    "xvinsgr2vr.w": 2,
    "vinsgr2vr.d": 2,
    "vinsgr2vr.w": 2,
    "vinsgr2vr.h": 2,
    "vinsgr2vr.b": 2,
    "vreplve.d": 2,
    "vreplve.w": 2,
    "vreplve.h": 2,
    "vreplve.b": 2,
    "xvpickve2gr.du": 2,
    "xvpickve2gr.wu": 2,
    "vpickve2gr.du": 2,
    "vpickve2gr.wu": 2,
    "vpickve2gr.hu": 2,
    "vpickve2gr.bu": 2,
    "xvftintrzl.l.s": 2,
    "xvftintrzh.l.s": 2,
    "xvftintrz.w.d": 2,
    "xvftintrpl.l.s": 2,
    "xvftintrph.l.s": 2,
    "xvftintrp.w.d": 2,
    "xvftintrnel.l.s": 2,
    "xvftintrneh.l.s": 2,
    "xvftintrne.w.d": 2,
    "xvftintrml.l.s": 2,
    "xvftintrmh.l.s": 2,
    "xvftintrm.w.d": 2,
    "xvftintl.l.s": 2,
    "xvftinth.l.s": 2,
    "xvftint.w.d": 2,
    "xvffintl.d.w": 2,
    "xvffinth.d.w": 2,
    "xvffint.s.l": 2,
    "xvfcvtl.s.h": 2,
    "xvfcvtl.d.s": 2,
    "xvfcvth.s.h": 2,
    "xvfcvth.d.s": 2,
    "xvfcvt.s.d": 2,
    "xvfcvt.h.s": 2,
    "vftintrzl.l.s": 2,
    "vftintrzh.l.s": 2,
    "vftintrz.w.d": 2,
    "vftintrpl.l.s": 2,
    "vftintrph.l.s": 2,
    "vftintrp.w.d": 2,
    "vftintrnel.l.s": 2,
    "vftintrneh.l.s": 2,
    "vftintrne.w.d": 2,
    "vftintrml.l.s": 2,
    "vftintrmh.l.s": 2,
    "vftintrm.w.d": 2,
    "vftintl.l.s": 2,
    "vftinth.l.s": 2,
    "vftint.w.d": 2,
    "vffintl.d.w": 2,
    "vffinth.d.w": 2,
    "vffint.s.l": 2,
    "vfcvtl.s.h": 2,
    "vfcvtl.d.s": 2,
    "vfcvth.s.h": 2,
    "vfcvth.d.s": 2,
    "vfcvt.s.d": 2,
    "vfcvt.h.s": 2,
    "xvreplgr2vr.h": 2,
    "xvreplgr2vr.b": 2,
    "vreplgr2vr.h": 2,
    "vreplgr2vr.b": 2,
    "vpickve2gr.h": 2,
    "vpickve2gr.b": 2,
    "cpucfg": 0,
    "rdtimel.w": 2,
    "rdtimeh.w": 2,
    "rdtime.d": 2,
    "movgr2frh.w": 2,
    "vpickve2gr.w": 2,
    "vpickve2gr.d": 2,
    "xvpickve2gr.w": 2,
    "xvpickve2gr.d": 2,
    "llacq.w": 2,
    "llacq.d": 2,
    "sc.q": 2,
}
errors: list[str] = []
warnings: list[str] = []


def error(path: Path, message: str) -> None:
    errors.append(f"{path.relative_to(ROOT)}: {message}")


def warning(path: Path, message: str) -> None:
    warnings.append(f"{path.relative_to(ROOT)}: {message}")


def close(a: float, b: float, eps: float = EPS) -> bool:
    return abs(a - b) <= eps


def asm_instruction_count(asm: str) -> int:
    count = 0
    for part in re.split(r"[;\n]", asm):
        line = part.strip()
        if not line or line.startswith("#") or line.startswith("."):
            continue
        if line.endswith(":"):
            continue
        count += 1
    return count


def effective_measurement(data: dict, experiment: dict) -> dict:
    measurement = dict(data["measurement"])
    measurement.update(experiment.get("measurement", {}))
    return measurement


def check_command(path: Path, data: dict, experiment: dict) -> None:
    command = experiment["command"]
    if not command.startswith("./bench "):
        error(path, f"command does not start with ./bench: {command!r}")

    measurement = effective_measurement(data, experiment)
    for flag, key in (("--unroll", "unroll"), ("--iters", "iters"), ("--warmup", "warmup")):
        expected = f"{flag} {measurement[key]}"
        if expected not in command:
            error(path, f"command missing {expected!r}: {command!r}")

    if f"--asm '{experiment['asm']}'" not in command:
        error(path, f"command does not contain the experiment asm: {command!r}")
    init = experiment.get("init")
    if init and f"--init '{init}'" not in command:
        error(path, f"command does not contain the experiment init: {command!r}")
    if not init and "--init " in command:
        error(path, f"command has --init but experiment has no init: {command!r}")


def instruction_multiplier(mnemonic: str, line: str, uarch: str) -> int:
    if uarch == "la464":
        # LA464 counts CPUCFG normally, retires both halves of RDTIME even
        # when the timer-ID destination is $r0, and splits FSEL into 2 uops.
        if mnemonic == "cpucfg":
            return 1
        if mnemonic in {"rdtimel.w", "rdtimeh.w", "rdtime.d"}:
            return 2
        if mnemonic == "fsel":
            return 2
    if mnemonic == "cpucfg":
        return 0
    if mnemonic in {"rdtimel.w", "rdtimeh.w", "rdtime.d"}:
        return 1 if ", $r0" in line else 2
    if mnemonic.startswith("am"):
        return 2
    if mnemonic.startswith(("vssra", "vssrl", "xvssra", "xvssrl")) and not mnemonic.endswith((".d.q", ".du.q")):
        return 2
    return RETIRED_MULTIPLIER.get(mnemonic, 1)


def expected_instruction_count(asm: str, uarch: str) -> int:
    total = 0
    for part in re.split(r"[;\n]", asm):
        line = part.strip()
        if not line or line.startswith("#") or line.startswith("."):
            continue
        if line.endswith(":"):
            continue
        total += instruction_multiplier(line.split()[0], line, uarch)
    return total


def check_result_count(path: Path, data: dict, experiment: dict) -> None:
    expected = expected_instruction_count(experiment["asm"], path.parent.name)
    loops = experiment.get("loops")
    loop_instructions = experiment.get("loop_instructions")
    if loops and loop_instructions:
        expected = expected - loop_instructions + loops * loop_instructions
    actual = experiment["result"]["instructions_retired"]
    if not close(actual, expected):
        error(path, f"instructions_retired {actual} != expected {expected} for {experiment.get('name', '<unnamed>')}")


def check_experiment(path: Path, data: dict, experiment: dict) -> None:
    check_command(path, data, experiment)
    check_result_count(path, data, experiment)
    result = experiment["result"]
    if result["core_cycles"] <= 0:
        error(path, f"core_cycles must be positive for {experiment.get('name', '<unnamed>')}")

    if experiment.get("chain_instructions"):
        for item in experiment["chain_instructions"]:
            if not item.strip():
                error(path, "empty entry in chain_instructions")


def check_operand_syntax(path: Path, data: dict) -> None:
    """The form must list exactly the syntactic operands; the rest are implicit."""
    name = data["instruction"]["name"]
    form = data["instruction"]["form"]
    if not form.startswith(name):
        error(path, f"form {form!r} does not start with the instruction name")
        return
    tokens = [t.strip() for t in form[len(name):].split(",") if t.strip()]
    syntactic = [op for op in data["operands"] if not op.get("implicit")]
    if len(tokens) != len(syntactic):
        error(path, f"form lists {len(tokens)} operand(s) {tokens} but {len(syntactic)} operand(s) are not implicit")
        return
    for token, op in zip(tokens, syntactic):
        if token != op["name"]:
            error(path, f"form operand {token!r} does not match operand name {op['name']!r}")
    for op in data["operands"]:
        if op.get("implicit") and op["kind"] not in ("flags", "other", "immediate", "gpr", "fpr", "vector"):
            error(path, f"implicit operand {op['name']} has unexpected kind {op['kind']}")
        if op["kind"] == "flags" and not op.get("implicit"):
            error(path, f"flags operand {op['name']} must be marked implicit")


def check_cross_uarch_forms(path: Path, data: dict) -> None:
    """The same instruction must keep one form across microarchitectures."""
    name = data["instruction"]["name"]
    form = data["instruction"]["form"]
    for other in DATA_DIR.glob(f"*/{name}.json"):
        if other == path:
            continue
        odata = json.loads(other.read_text())
        oform = odata["instruction"]["form"]
        if oform != form:
            error(path, f"form {form!r} differs from {other.parent.name} ({oform!r})")


def check_operands(path: Path, data: dict) -> None:
    operands = data["operands"]
    indices = [op["index"] for op in operands]
    if len(indices) != len(set(indices)):
        error(path, "duplicate operand indices")
    if sorted(indices) != list(range(1, len(indices) + 1)):
        error(path, f"operand indices are not contiguous from 1: {indices}")

    by_index = {op["index"]: op for op in operands}

    for latency in data["latencies"]:
        targets = [latency["to_operand"]]
        sources = latency["from_operands"]
        for index in sources + targets:
            if index not in by_index:
                error(path, f"latency references missing operand {index}")
        if latency["to_operand"] not in by_index:
            continue
        target = by_index[latency["to_operand"]]
        if target["access"] not in ("write", "read-write"):
            error(path, f"latency target operand {latency['to_operand']} is not writable")
        for index in sources:
            if index not in by_index:
                continue
            source = by_index[index]
            if source["access"] not in ("read", "read-write"):
                error(path, f"latency source operand {index} is not readable")


def check_latencies(path: Path, data: dict) -> None:
    for latency in data["latencies"]:
        experiments = latency["experiments"]
        for experiment in experiments:
            check_experiment(path, data, experiment)

        measurements = [e for e in experiments if e.get("role", "measurement") == "measurement"]
        if len(measurements) != 1:
            error(path, f"latency {latency['from_operands']}->{latency['to_operand']} has {len(measurements)} measurement experiments (expected 1)")
            continue
        experiment = measurements[0]
        expected = experiment["result"]["core_cycles"]
        if "chain_latency" in experiment:
            expected -= experiment["chain_latency"]
        expected /= latency.get("cycles_divisor", 1)
        # LA464 values are rounded to the nearest integer when the residual
        # measurement noise is below 0.06 cycle.
        eps = 0.45 if path.parent.name == "la464" else EPS
        if not close(latency["cycles"], expected, eps):
            error(path, f"latency cycles {latency['cycles']} != core_cycles - chain_latency ({expected})")

        cases = latency.get("cases")
        if cases:
            values = [case["cycles"] for case in cases]
            low, high = min(values), max(values)
            if "cycles_min" in latency and not close(latency["cycles_min"], low, EPS):
                error(path, f"latency cycles_min {latency['cycles_min']} != smallest case ({low})")
            if "cycles_max" in latency and not close(latency["cycles_max"], high, EPS):
                error(path, f"latency cycles_max {latency['cycles_max']} != largest case ({high})")
            if not (low <= latency["cycles"] <= high):
                error(path, f"latency cycles {latency['cycles']} is outside the case range {low}-{high}")


def check_throughput(path: Path, data: dict) -> None:
    for experiment in data.get("throughput", []):
        check_experiment(path, data, experiment)
        throughput = experiment.get("throughput")
        if not throughput:
            error(path, f"throughput experiment {experiment.get('name', '<unnamed>')} has no throughput object")
            continue
        lanes = throughput["lanes"]
        result = experiment["result"]
        expected_cpi = (result["core_cycles"] - experiment.get("baseline_cycles", 0)) / lanes
        eps = 0.06 if path.parent.name == "la464" else EPS
        if not close(throughput["cpi"], expected_cpi, eps):
            error(path, f"throughput CPI {throughput['cpi']} != core_cycles/lanes ({expected_cpi})")
        if not close(throughput["ipc"], 1.0 / throughput["cpi"]):
            error(path, f"throughput IPC {throughput['ipc']} != 1/CPI")


def check_file(path: Path, schema: dict) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        error(path, f"invalid JSON: {exc}")
        return

    validator = Draft202012Validator(schema)
    schema_errors_before = len(errors)
    for item in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        location = ".".join(str(part) for part in item.path) or "<root>"
        error(path, f"schema error at {location}: {item.message}")

    if len(errors) != schema_errors_before:
        # Do not continue with semantic checks if the basic shape is wrong.
        return

    uarch = path.parent.name
    name = data["instruction"]["name"]
    if path.stem != name:
        error(path, f"filename stem {path.stem!r} != instruction name {name!r}")
    check_operands(path, data)
    check_operand_syntax(path, data)
    check_cross_uarch_forms(path, data)
    check_latencies(path, data)
    check_throughput(path, data)

    if not data["latencies"] and not data.get("notes"):
        warning(path, "no latency data and no notes explaining why")


def main() -> int:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    files = sorted(DATA_DIR.glob("*/*.json"))
    if not files:
        print(f"No JSON files found under {DATA_DIR.relative_to(ROOT)}", file=sys.stderr)
        return 1

    names: dict[tuple[str, str], Path] = {}
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            key = (path.parent.name, data["instruction"]["name"])
        except Exception:
            key = None
        if key is not None:
            if key in names:
                error(path, f"duplicate instruction {key[1]} for {key[0]} (also {names[key].relative_to(ROOT)})")
            names[key] = path

    for path in files:
        check_file(path, schema)

    for message in warnings:
        print(f"WARNING: {message}")
    for message in errors:
        print(f"ERROR: {message}")

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1

    print(f"OK: {len(files)} file(s), {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
