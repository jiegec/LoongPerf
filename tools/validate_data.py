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
DOCS_DIR = ROOT / "docs"
SCHEMA_PATH = ROOT / "schema" / "instruction.schema.json"

EPS = 0.02

TWO_UOP_INSTRUCTIONS = set("""vpickve2gr.b vpickve2gr.h vreplgr2vr.b vreplgr2vr.h xvreplgr2vr.b xvreplgr2vr.h vfcvt.h.s vfcvt.s.d vfcvth.d.s vfcvth.s.h vfcvtl.d.s vfcvtl.s.h vffint.s.l vffinth.d.w vffintl.d.w vftint.w.d vftinth.l.s vftintl.l.s vftintrm.w.d vftintrmh.l.s vftintrml.l.s vftintrne.w.d vftintrneh.l.s vftintrnel.l.s vftintrp.w.d vftintrph.l.s vftintrpl.l.s vftintrz.w.d vftintrzh.l.s vftintrzl.l.s xvfcvt.h.s xvfcvt.s.d xvfcvth.d.s xvfcvth.s.h xvfcvtl.d.s xvfcvtl.s.h xvffint.s.l xvffinth.d.w xvffintl.d.w xvftint.w.d xvftinth.l.s xvftintl.l.s xvftintrm.w.d xvftintrmh.l.s xvftintrml.l.s xvftintrne.w.d xvftintrneh.l.s xvftintrnel.l.s xvftintrp.w.d xvftintrph.l.s xvftintrpl.l.s xvftintrz.w.d xvftintrzh.l.s xvftintrzl.l.s""".split())

RETIRED_MULTIPLIER = {
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
    if mnemonic in TWO_UOP_INSTRUCTIONS:
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
        if not close(latency["cycles"], expected):
            error(path, f"latency cycles {latency['cycles']} != core_cycles - chain_latency ({expected})")


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
        if not close(throughput["cpi"], expected_cpi):
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
    page = DOCS_DIR / uarch / f"{name}.md"
    if not page.exists():
        error(path, f"missing instruction page {page.relative_to(ROOT)}")

    check_operands(path, data)
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
