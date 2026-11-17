#!/usr/bin/env python3
"""Build the LoongPerf documentation site.

Zensical builds the normal Markdown pages (index, methodology, table) and
copies the measurement JSON.  The per-instruction pages are generated
directly from ``docs/data/*/*.json`` instead of rendering 3900 Markdown
files through Zensical, which keeps a full build in the low seconds.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
AGGREGATE_PATH = DATA_DIR / "measurements.json"
SITE_DIR = ROOT / "site"
TEMPLATE_PAGE = SITE_DIR / "la464" / "_instruction_template" / "index.html"

CONFIG = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


def config_value(key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)\s*$", CONFIG, re.MULTILINE)
    if not match:
        raise SystemExit(f"missing {key} in mkdocs.yml")
    return match.group(1)


SITE_URL = config_value("site_url").rstrip("/")
REPO_URL = config_value("repo_url")


# ---------------------------------------------------------------------------
# Aggregate index for table.html
# ---------------------------------------------------------------------------


def table_entry(uarch: str, source: str, data: dict) -> dict:
    ins = data["instruction"]
    operands = [
        {key: op[key] for key in ("index", "name", "kind", "access") if key in op}
        for op in data.get("operands", [])
    ]
    latencies = []
    for item in data.get("latencies", []):
        latency = {
            "from_operands": item["from_operands"],
            "to_operand": item["to_operand"],
            "cycles": item.get("cycles"),
        }
        if "cycles_min" in item:
            latency["cycles_min"] = item["cycles_min"]
        if "cycles_max" in item:
            latency["cycles_max"] = item["cycles_max"]
        if item.get("cases"):
            latency["cases"] = [
                {"name": case["name"], "cycles": case["cycles"]} for case in item["cases"]
            ]
        latencies.append(latency)
    throughput = []
    for item in data.get("throughput", []):
        stats = item.get("throughput", {})
        result = item.get("result", {})
        experiment = {
            "name": item.get("name", ""),
            "asm": item.get("asm", ""),
            "throughput": {
                key: stats[key]
                for key in ("cpi", "cpi_min", "cpi_max", "ipc", "ipc_min", "ipc_max", "lanes")
                if key in stats and stats[key] is not None
            },
            "result": {
                "instructions_retired": result.get("instructions_retired"),
                "core_cycles": result.get("core_cycles"),
            },
        }
        if result.get("counters"):
            experiment["result"]["counters"] = result["counters"]
        throughput.append(experiment)
    entry = {
        "uarch": uarch,
        "source": source,
        "extension": ins.get("extension", ""),
        "name": ins["name"],
        "form": ins["form"],
        "description": ins["description"],
        "operands": operands,
        "latencies": latencies,
        "throughput": throughput,
    }
    if data.get("notes"):
        entry["notes"] = data["notes"]
    return entry


def last_data_commit() -> dict | None:
    try:
        result = subprocess.run(
            [
                "git", "-C", str(ROOT), "log", "-1",
                "--format=%H%x00%h%x00%cI", "--", "docs/data",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("\0")
    if len(parts) != 3:
        return None
    sha, short, date = parts
    return {
        "sha": sha,
        "short": short,
        "date": date,
        "url": f"{REPO_URL}/commit/{sha}",
    }


def data_metadata() -> dict:
    metadata = {"repo": REPO_URL}
    commit = last_data_commit()
    if commit:
        metadata["updated"] = commit["date"]
        metadata["commit"] = commit
    return metadata


def write_aggregate() -> None:
    entries = []
    for path in sorted(DATA_DIR.glob("*/*.json")):
        uarch = path.parent.name
        entries.append(table_entry(
            uarch,
            f"data/{uarch}/{path.name}",
            json.loads(path.read_text()),
        ))
    AGGREGATE_PATH.write_text(
        json.dumps(
            {"metadata": data_metadata(), "instructions": entries},
            separators=(",", ":"),
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Instruction page HTML
# ---------------------------------------------------------------------------


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def fmt_range(obj: dict, key: str) -> str:
    lo = obj.get(key + "_min", obj.get(key))
    hi = obj.get(key + "_max", obj.get(key))
    if lo is None or hi is None:
        return ""
    if lo == hi:
        return fmt(lo)
    return f"{fmt(lo)} – {fmt(hi)}"


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


def cases_summary(item: dict) -> str:
    cases = item.get("cases") or []
    if not cases:
        return ""
    return " / ".join(f"{case['cycles']:g}" for case in cases)


def table_html(headers, rows) -> str:
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{esc(header)}</th>" for header in headers]
    out += ["</tr></thead>", "<tbody>"]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    out += ["</tbody></table>"]
    return "".join(out)


def code_block(text) -> str:
    return f"<pre><code>{esc(text)}</code></pre>" if text else ""


def para(text) -> str:
    if not text:
        return ""
    return "<p>" + esc(text).replace("\n", "<br>") + "</p>"


def render_experiment(exp: dict, level: str = "h3") -> list[str]:
    out = [f'<{level}>{esc(exp.get("name", "Experiment"))}</{level}>']
    if exp.get("role"):
        out += [f'<p><strong>Role:</strong> <code>{esc(exp["role"])}</code></p>']
    if exp.get("asm"):
        out += ["<p><strong>Instruction sequence:</strong></p>", code_block(exp["asm"])]
    if exp.get("init"):
        out += ["<p><strong>Init:</strong></p>", code_block(exp["init"])]
    if exp.get("measurement"):
        parts = [f"{k} {v}" for k, v in exp["measurement"].items() if k != "notes"]
        out += [f'<p><strong>Measurement override:</strong> {esc(", ".join(parts))}</p>']
    if exp.get("chain_instructions"):
        out += ["<p><strong>Chain instructions:</strong></p>",
                code_block("\n".join(exp["chain_instructions"]))]
    if "chain_latency" in exp:
        out += [f'<p><strong>Chain latency:</strong> {esc(fmt(exp["chain_latency"]))} cycles</p>']
    if exp.get("dependency_breaking"):
        out += ["<p><strong>Dependency breaking:</strong> yes</p>"]
    if exp.get("machine_code"):
        out += ["<p><strong>Machine code:</strong></p>",
                code_block("\n".join(exp["machine_code"]))]
    if exp.get("command"):
        out += ["<p><strong>Command:</strong></p>", code_block(exp["command"])]
    result = exp.get("result", {})
    rows = [
        ["Instructions retired", esc(fmt(result.get("instructions_retired")))],
        ["Core cycles", esc(fmt(result.get("core_cycles")))],
    ]
    if "reference_cycles" in result:
        rows.append(["Reference cycles", esc(fmt(result["reference_cycles"]))])
    for key, value in result.get("counters", {}).items():
        rows.append([esc(key), esc(fmt(value))])
    out += ["<p><strong>Result:</strong></p>", table_html(["Counter", "Value"], rows)]
    if exp.get("notes"):
        out += [para(exp["notes"])]
    return out


def render_instruction(uarch: str, data: dict) -> str:
    ins = data["instruction"]
    out = [f'<h1>{esc(ins["name"])}</h1>']
    out += [f'<p><strong>Microarchitecture:</strong> <code>{esc(uarch.upper())}</code></p>']
    out += [f'<p><strong>Extension:</strong> <code>{esc(ins.get("extension", ""))}</code></p>']
    out += [f'<p><strong>Form:</strong> <code>{esc(ins["form"])}</code></p>']
    out += [para(ins["description"])]
    m = data["measurement"]
    out += [f'<p><strong>Measurement defaults:</strong> unroll {esc(m["unroll"])}, '
            f'iters {esc(m["iters"])}, warmup {esc(m["warmup"])}, '
            f'reps {esc(m["reps"])}, core {esc(m["core"])}.</p>']

    out += ["<h2>Operands</h2>"]
    out += [table_html(
        ["#", "Operand", "Kind", "Access", "Notes"],
        [[esc(op["index"]), f'<code>{esc(op["name"])}</code>', esc(op["kind"]),
          esc(op["access"]), esc(operand_note(op))]
         for op in data["operands"]],
    )]

    out += ["<h2>Latency</h2>"]
    out += [table_html(
        ["From", "To", "Latency (cycles)", "Depends on the operands"],
        [[esc(operand_ref(data, item["from_operands"])),
          esc(operand_ref(data, [item["to_operand"]])),
          esc(fmt_range(item, "cycles")),
          esc(cases_summary(item))]
         for item in data.get("latencies", [])],
    )]

    if data.get("throughput"):
        rows = []
        for item in data["throughput"]:
            t = item.get("throughput", {})
            r = item.get("result", {})
            extra = ", ".join(f"{k}={fmt(v)}" for k, v in r.get("counters", {}).items())
            rows.append([
                esc(item.get("name", "")),
                esc(fmt(t.get("lanes"))),
                esc(fmt(r.get("instructions_retired"))),
                esc(fmt(r.get("core_cycles"))),
                esc(fmt_range(t, "cpi")),
                esc(fmt_range(t, "ipc")),
                esc(extra),
            ])
        out += ["<h2>Throughput</h2>"]
        out += [table_html(
            ["Experiment", "Lanes", "Instructions retired", "Core cycles",
             "CPI", "IPC", "Extra counters"],
            rows,
        )]

    out += ["<h2>Latency details</h2>"]
    for item in data.get("latencies", []):
        out += [f'<h3>{esc(operand_ref(data, item["from_operands"]))} -> '
                f'{esc(operand_ref(data, [item["to_operand"]]))}: '
                f'{esc(fmt(item["cycles"]))} cycles</h3>']
        if item.get("cases"):
            rows = []
            for case in item["cases"]:
                experiment = case.get("experiment") or {}
                setup = experiment.get("init", "")
                commands = f'<code>{esc(experiment["command"])}</code>' if experiment.get("command") else ""
                rows.append([esc(case["name"]), esc(fmt(case["cycles"])),
                             f'<code>{esc(setup)}</code>' if setup else "",
                             commands])
            out += [table_html(["Case", "Cycles", "Operand setup", "Command"], rows)]
        if item.get("notes"):
            out += [para(item["notes"])]
        for exp in item.get("experiments", []):
            out += render_experiment(exp, "h4")

    if data.get("throughput"):
        out += ["<h2>Throughput details</h2>"]
        for exp in data["throughput"]:
            out += render_experiment(exp, "h3")

    if data.get("notes"):
        out += ["<h2>Notes</h2>", para(data["notes"])]

    return '<article class="md-content__inner md-typeset">' + "".join(out) + "</article>"


def extract_shell() -> tuple[str, str]:
    shell = TEMPLATE_PAGE.read_text(encoding="utf-8")
    start = shell.index('<article class="md-content__inner md-typeset">')
    end = shell.index("</article>", start) + len("</article>")
    return shell[:start], shell[end:]


def generate_instruction_pages(prefix: str, suffix: str) -> int:
    count = 0
    for uarch_dir in sorted(DATA_DIR.iterdir()):
        if not uarch_dir.is_dir():
            continue
        uarch = uarch_dir.name
        for json_path in sorted(uarch_dir.glob("*.json")):
            data = json.loads(json_path.read_text())
            ins = data["instruction"]
            name = ins["name"]
            page_prefix = prefix.replace("Instruction template", esc(name))
            page_prefix = page_prefix.replace(
                f"{SITE_URL}/la464/_instruction_template/",
                f"{SITE_URL}/{uarch}/{name}/",
            )
            page_prefix = re.sub(
                r'<meta name="description" content="[^"]*"',
                f'<meta name="description" content="{esc(ins["description"])}"',
                page_prefix,
                count=1,
            )
            page = page_prefix + render_instruction(uarch, data) + suffix
            target = SITE_DIR / uarch / name / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
            count += 1
    return count


def clean_generated_shell() -> None:
    shutil.rmtree(SITE_DIR / "la464" / "_instruction_template", ignore_errors=True)

    sitemap = SITE_DIR / "sitemap.xml"
    if sitemap.is_file():
        text = sitemap.read_text(encoding="utf-8")
        text = re.sub(
            r"\s*<url>\s*<loc>[^<]*_instruction_template[^<]*</loc>\s*</url>",
            "",
            text,
        )
        sitemap.write_text(text, encoding="utf-8")

    search = SITE_DIR / "search.json"
    if search.is_file():
        payload = json.loads(search.read_text())
        items = [
            item for item in payload.get("items", [])
            if "_instruction_template" not in json.dumps(item, ensure_ascii=False)
        ]
        if len(items) != len(payload.get("items", [])):
            payload["items"] = items
            search.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    t0 = time.perf_counter()
    write_aggregate()
    t1 = time.perf_counter()

    subprocess.run(
        [sys.executable, "-m", "zensical", "build", "--clean", "--strict"],
        cwd=ROOT,
        check=True,
    )
    t2 = time.perf_counter()

    prefix, suffix = extract_shell()
    count = generate_instruction_pages(prefix, suffix)
    clean_generated_shell()
    t3 = time.perf_counter()

    print(
        f"aggregate {t1 - t0:.2f}s, zensical {t2 - t1:.2f}s, "
        f"instructions {t3 - t2:.2f}s ({count} pages), "
        f"total {t3 - t0:.2f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
