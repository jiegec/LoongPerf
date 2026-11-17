# LoongPerf

**uops.info for LoongArch.**

LoongPerf aims to build an open and reproducible database of instruction-level
performance data for LoongArch microarchitectures. Inspired by x86
[uops.info](https://uops.info/) and nanoBench, it measures characteristics such
as latency and throughput of individual instructions on real hardware, across
different LoongArch cores, and makes the results searchable and comparable.

## Building the site

The documentation site is built by `tools/build_site.py`. It uses Zensical for
the Markdown pages (overview, methodology, analysis and table) and generates the
per-instruction HTML pages directly from the measurement JSON, so adding an
instruction does not require a corresponding Markdown file:

```sh
uv sync
uv run python tools/build_site.py
```

The generated site is written to `site/`.
