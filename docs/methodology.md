---
hide:
  - navigation
---
# Methodology

Measurements are produced with `./bench`, a small nanoBench-style tool for
LoongArch.

`./bench` assembles the given instruction sequence, unrolls it, warms it up,
and executes it in a tight loop. It uses `perf_event_open(2)` to count retired
instructions and core cycles, and subtracts the overhead of an empty function
measured with the same loop.

Latency is measured with dependency chains: the instruction under test is
followed by chain instructions whose latency is known, and the chain latency
is subtracted from the measured total. Throughput is measured with independent
lanes.

Each raw measurement is stored as JSON under `docs/data/<microarchitecture>/`.
Every experiment records:

- the assembly sequence
- the init sequence
- the exact command line
- machine code
- the measured instruction and cycle counts
- latency operand pair, or throughput lanes

Latency entries use operand indices from the `operands` list. For example,
`from_operands: [2]` and `to_operand: 1` means the latency from operand 2 to
operand 1.

The JSON format is defined by `schema/instruction.schema.json`.

## Custom performance counters

`./bench` can also read raw LoongArch PMU counter configs with `--counter`.
The option is repeatable; at most two extra counters can be added because the
PMU has four counters total (instructions, cycles, and two extras).

```sh
./bench --unroll 1000 --iters 1000 --counter 0xfb --counter 0xa8 \
  --init 'li.d $r4, 1; vreplgr2vr.b $vr0, $r4' \
  --asm 'vadd.b $vr0, $vr0, $vr0'
```

Each extra counter is printed as:

```text
counter 0xfb: 1.00
counter 0xa8: 0.00
```

Counts are normalised per execution of the unrolled sequence and have the
empty-function baseline subtracted, the same way as `instructions retired` and
`cycles`.

### Useful raw counters on LA664

These are vendor events outside the public Linux event map. The meanings below
are inferred from controlled instruction-mix experiments.

| Config | Meaning |
| --- | --- |
| `0x01` | Retired uops (this is what `PERF_COUNT_HW_INSTRUCTIONS` counts on this machine) |
| `0xfb` | LSX vector uops |
| `0xfa` | LASX vector uops |
| `0xfc` | Scalar floating-point uops |
| `0xa8` | Load operations (one per load instruction, not per uop) |
| `0xb0` | Store operations (one per store instruction, not per uop) |

`0x01` is a uop count, not an architectural instruction count. In particular,
an instruction such as `vpickve2gr.w` retires as two uops and shows up as
`2.00`. To obtain architectural instruction counts, count the assembly
instructions statically and compare them with `0x01`.

`0xa8` and `0xb0` are memory-operation counters, not uop counters. Atomics make
the distinction clear: `amadd.w` retires as two uops, but contributes one load
and one store. `llacq.w` contributes one load, and `sc.q` contributes one
store, even though each is a two-uop instruction.

### Useful raw counters on LA464

The LA464 (Loongson-3C5000) PMU uses a different raw event map. The LA664
configs `0xfb`, `0xfa`, and `0xfc` read zero on LA464; use these instead:

| Config | Meaning |
| --- | --- |
| `0x01` | Retired uops (same meaning as on LA664) |
| `0x30` | Scalar floating-point uops |
| `0x25` | Vector instructions (LSX and LASX combined, one per instruction) |
| `0x31` | Vector uops (LSX and LASX combined) |
| `0xa8` | Load operations (same meaning as on LA664) |
| `0xb0` | Store operations (same meaning as on LA664) |

On LA464, `0x25` counts each LSX/LASX instruction once, while `0x31` counts
the vector uops. For example, `vadd.b` gives `0x25 = 1` and `0x31 = 1`, but a
two-uop conversion such as `vfcvt.h.s` or `xvfcvt.h.s` gives `0x25 = 1` and
`0x31 = 2`. A scan of configs `0x00`-`0xff` did not find separate LSX and
LASX uop counters on LA464, so `0x31` is the combined vector-uop counter.

Scalar floating-point examples for `0x30`: `fadd.d = 1`, `fdiv.d = 1`,
`movgr2frh.w = 1`, and `fsel = 2`. Integer instructions count as zero.

## Multi-uop instructions

`0x01` counts retired uops, so a multi-uop instruction has a counter value
larger than its static instruction count. In the current LA664 data set 104 of
1260 measured instructions are two-uop instructions:

| Category | Count |
| --- | ---: |
| Atomic `am*` | 52 |
| `vfcvt` / `vffint` / `vftint` conversions, LSX and LASX | 48 |
| `llacq.d`, `llacq.w`, `sc.q` | 3 |
| `movgr2frh.w` | 1 |

For these instructions the throughput experiment also records the relevant
extra counters in `result.counters`:

- atomics: `0xa8` (one load) and `0xb0` (one store), even though the atomic
  retires as two uops;
- `llacq.*`: `0xa8` (one load);
- `sc.q`: `0xb0` (one store);
- `vfcvt` / `vffint` / `vftint` LSX: `0xfb` (two LSX uops);
- the same LASX conversions: `0xfa` (two LASX uops);
- `movgr2frh.w`: `0xfc` (one scalar floating-point uop).

The extra counter values are normalised per instruction (the measured
per-sequence values divided by the eight lanes). They are shown in the
per-instruction throughput table and in the interactive table details.

The following instructions are known to be two-uop from the reset helper, but
do not yet have their own pages: `vpickve2gr.b/h/w/d`, `vreplgr2vr.b/h`, and
`xvpickve2gr.w/d`.
