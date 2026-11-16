"""Zensical macros that render docs/data/<uarch>/<instruction>.json."""

from __future__ import annotations

import html
import json
from pathlib import Path

DATA_DIR: Path
AGGREGATE_PATH: Path


def define_env(env):
    global DATA_DIR, AGGREGATE_PATH
    project_dir = Path(env.project_dir).resolve()
    DATA_DIR = project_dir / "docs" / "data"
    site_dir = Path(env.conf.get("site_dir", "site"))
    if not site_dir.is_absolute():
        site_dir = project_dir / site_dir
    AGGREGATE_PATH = site_dir / "data" / "measurements.json"

    @env.macro
    def instruction_index() -> str:
        write_aggregate()
        rows = []
        for path in sorted(DATA_DIR.glob("*/*.json")):
            uarch = path.parent.name
            data = json.loads(path.read_text())
            name = data["instruction"]["name"]
            latencies = "; ".join(
                f"{operand_ref(data, item['from_operands'])} -> "
                f"{operand_ref(data, [item['to_operand']])}: "
                f"{fmt(item['cycles'])}"
                for item in data.get("latencies", [])
            )
            tput = min(
                (item["throughput"]["cpi"] for item in data.get("throughput", [])
                 if "throughput" in item),
                default=None,
            )
            rows.append(
                [
                    uarch,
                    f"`{name}`",
                    latencies,
                    fmt(tput) if tput is not None else "",
                    f"[details]({uarch}/{name}.md)",
                ]
            )
        return table(
            ["Microarchitecture", "Instruction", "Latency (cycles)",
             "Throughput (CPI)", "Details"],
            rows,
        )

    @env.macro
    def render_instruction(uarch: str, name: str) -> str:
        path = DATA_DIR / uarch / f"{name}.json"
        data = json.loads(path.read_text())
        out: list[str] = []

        ins = data["instruction"]
        out += [f"# `{ins['name']}`", ""]
        out += [f"**Microarchitecture:** `{uarch}`", ""]
        out += [f"**Form:** `{ins['form']}`", ""]
        out += [ins["description"], ""]
        m = data["measurement"]
        out += [
            "**Measurement defaults:** "
            f"unroll {m['unroll']}, iters {m['iters']}, warmup {m['warmup']}, "
            f"reps {m['reps']}, core {m['core']}.",
            "",
        ]

        out += ["## Operands", ""]
        out += [table(
            ["#", "Operand", "Kind", "Access", "Notes"],
            [
                [
                    str(op["index"]),
                    f"`{op['name']}`",
                    op["kind"],
                    op["access"],
                    operand_note(op),
                ]
                for op in data["operands"]
            ],
        ), ""]

        out += ["## Latency", ""]
        out += [table(
            ["From", "To", "Latency (cycles)"],
            [
                [
                    operand_ref(data, item["from_operands"]),
                    operand_ref(data, [item["to_operand"]]),
                    fmt(item["cycles"]),
                ]
                for item in data.get("latencies", [])
            ],
        ), ""]

        if data.get("throughput"):
            out += ["## Throughput", ""]
            out += [table(
                ["Experiment", "Lanes", "Instructions retired",
                 "Core cycles", "CPI", "IPC"],
                [
                    [
                        item.get("name", ""),
                        str(item.get("throughput", {}).get("lanes", "")),
                        fmt(item["result"]["instructions_retired"]),
                        fmt(item["result"]["core_cycles"]),
                        fmt(item.get("throughput", {}).get("cpi")),
                        fmt(item.get("throughput", {}).get("ipc")),
                    ]
                    for item in data["throughput"]
                ],
            ), ""]

        out += ["## Latency details", ""]
        for item in data.get("latencies", []):
            out += [
                f"### {operand_ref(data, item['from_operands'])} -> "
                f"{operand_ref(data, [item['to_operand']])}: "
                f"{fmt(item['cycles'])} cycles",
                "",
            ]
            if item.get("notes"):
                out += [item["notes"], ""]
            for exp in item["experiments"]:
                out += render_experiment(exp, heading="####")
                out += [""]

        if data.get("throughput"):
            out += ["## Throughput details", ""]
            for exp in data["throughput"]:
                out += render_experiment(exp, heading="###")
                out += [""]

        if data.get("notes"):
            out += ["## Notes", "", data["notes"], ""]

        return "\n".join(out)


def render_experiment(exp: dict, heading: str = "###") -> list[str]:
    out = [f"{heading} {exp.get('name', 'Experiment')}", ""]
    if exp.get("role"):
        out += [f"**Role:** `{exp['role']}`", ""]
    out += ["**Instruction sequence:**", "", code_block("asm", exp["asm"]), ""]
    if exp.get("init"):
        out += ["**Init:**", "", code_block("asm", exp["init"]), ""]
    if exp.get("measurement"):
        m = exp["measurement"]
        parts = [f"{k} {v}" for k, v in m.items() if k != "notes"]
        out += ["**Measurement override:** " + ", ".join(parts), ""]
    if exp.get("chain_instructions"):
        out += [
            "**Chain instructions:**",
            "",
            code_block("asm", "\n".join(exp["chain_instructions"])),
            "",
        ]
    if "chain_latency" in exp:
        out += [f"**Chain latency:** {fmt(exp['chain_latency'])} cycles", ""]
    if exp.get("dependency_breaking"):
        out += ["**Dependency breaking:** yes", ""]
    if exp.get("machine_code"):
        out += ["**Machine code:**", "",
                code_block("text", "\n".join(exp["machine_code"])), ""]
    out += ["**Command:**", "", code_block("sh", exp["command"]), ""]
    out += ["**Result:**", ""]
    result = exp["result"]
    rows = [
        ["Instructions retired", fmt(result["instructions_retired"])],
        ["Core cycles", fmt(result["core_cycles"])],
    ]
    if "reference_cycles" in result:
        rows.append(["Reference cycles", fmt(result["reference_cycles"])])
    for key, value in result.get("counters", {}).items():
        rows.append([key, fmt(value)])
    out += [table(["Counter", "Value"], rows), ""]
    if exp.get("notes"):
        out += [exp["notes"], ""]
    return out


def write_aggregate() -> None:
    """Write one JSON file containing every measurement for table.html."""
    entries = []
    for path in sorted(DATA_DIR.glob("*/*.json")):
        uarch = path.parent.name
        entries.append({
            "uarch": uarch,
            "source": f"data/{uarch}/{path.name}",
            "data": json.loads(path.read_text()),
        })
    AGGREGATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGGREGATE_PATH.write_text(
        json.dumps({"instructions": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def operand_ref(data: dict, indices: list[int]) -> str:
    names = []
    for index in indices:
        for op in data["operands"]:
            if op["index"] == index:
                names.append(op["name"])
                break
    return f"{', '.join(names)} ({', '.join(str(i) for i in indices)})"


def operand_note(op: dict) -> str:
    parts = []
    if op.get("implicit"):
        parts.append("implicit")
    if op.get("suppressed"):
        parts.append("suppressed")
    if op.get("flags"):
        parts.append(", ".join(f"{k}: {v}" for k, v in op["flags"].items()))
    if op.get("description"):
        parts.append(op["description"])
    return "; ".join(parts)


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def code_block(lang: str, text: str) -> str:
    return f"```{lang}\n{text}\n```"


def table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        out.append("| " + " | ".join(cell(value) for value in row) + " |")
    return "\n".join(out)


def cell(value) -> str:
    return html.escape(str(value)).replace("|", "\\|")
