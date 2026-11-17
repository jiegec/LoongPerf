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
