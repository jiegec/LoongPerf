---
hide:
  - navigation
---
# Methodology

Measurements are produced with `./bench`, a small nanoBench-style tool for
LoongArch.

`./bench` assembles the given instruction sequence, unrolls it, warms it up,
and executes it in a tight loop, counting retired uops and core cycles with
`perf_event_open(2)`.

## Removing measurement overhead

Every measurement pays a fixed cost that does not belong to the instructions
under test: the call itself, the register-save prologue and epilogue, the
caller's loop branch, and the `perf_event_open` enable/disable and counter
reads. Subtracting a separately measured empty function is **not** a valid way
to remove this cost, because the empty function cannot overlap its prologue
with any work while a real body can, so the empty baseline always overstates
the real per-call cost.

`./bench` therefore defaults to the **slope method**: it compiles the same
sequence twice with different unroll factors and differences the two
measurements.

```text
C(k) = O + k * c          O = all per-call and per-measurement cost
c    = (C(k2) - C(k1)) / (k2 - k1)
```

`O` cancels exactly, because both measurements execute the same number of
calls and the same number of perf enable/disable cycles. `--unroll N` sets the
smaller body and `--unroll2 N2` the larger one (default `2N`). The reported
value is normalised by `iters * (N2 - N)`.

This matters most at small unroll factors. For a single dependent `add.d`
(1 cycle) the raw body measurement reads 0.89 at `--unroll 100` while the slope
method reads 0.98; for a `fadd.d` chain (3 cycles) the raw value is 2.23 at
`--unroll 100` and the slope method gives 3.00.

`--method raw` reports a single body measurement unchanged. It includes the
per-call overhead and is only useful as an upper bound or for comparing runs
made with identical settings. Earlier data in this repository was collected
with the old empty-function subtraction, so its recorded command lines do not
show `--method`; the values have since been normalised to nominal integers.

Latency is measured with dependency chains: the instruction under test is
followed by chain instructions whose latency is known, and the chain latency
is subtracted from the measured total. Throughput is measured with independent
lanes.

Each raw measurement is stored as JSON under `docs/data/<microarchitecture>/`.
Every experiment records:

- the assembly sequence
- the init sequence
- the exact command line
- the measured counter and cycle counts
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

### Common raw counters (LA464 and LA664)

These configs have the same meaning on both microarchitectures:

| Config | Meaning |
| --- | --- |
| `0x01` | Retired uops (`PERF_COUNT_HW_INSTRUCTIONS`) |
| `0xa8` | Load operations (not uops) |
| `0xb0` | Store operations (not uops) |

`0x01` is a uop count, not an architectural instruction count. On LA664, for
example, `vpickve2gr.w` retires as two uops and shows up as `2.00`. To obtain
architectural instruction counts, count the assembly instructions statically
and compare them with `0x01`.

`0xa8` and `0xb0` are memory-operation counters, not uop counters. On LA664,
atomics make the distinction clear: `amadd.w` retires as two uops, but
contributes one load and one store. `llacq.w` contributes one load, and `sc.q`
contributes one store, even though each is a two-uop instruction.

### LA664-specific raw counters

The following vendor events are specific to LA664 (Loongson-3C6000):

| Config | Meaning |
| --- | --- |
| `0xfb` | LSX vector uops |
| `0xfa` | LASX vector uops |
| `0xfc` | Scalar floating-point uops |

These are outside the public Linux event map. The meanings are inferred from
controlled instruction-mix experiments.

### LA464-specific raw counters

The LA464 (Loongson-3C5000) PMU uses a different raw event map. The LA664
configs `0xfb`, `0xfa`, and `0xfc` read zero on LA464; use the following
mappings instead:

| Config | Meaning |
| --- | --- |
| `0x30` | Scalar FP arithmetic/conversion uops |
| `0x25` | Vector ALU/conversion instructions (LSX and LASX combined) |
| `0x31` | Vector ALU/conversion uops (LSX and LASX combined) |

On LA464, `0x25` counts each LSX/LASX ALU or conversion instruction once,
while `0x31` counts the corresponding vector uops. For example, `vadd.b`,
`vand.v`, `vpickve2gr.w`, and `xvpickve2gr.w` give `0x25 = 1` and
`0x31 = 1`, but a two-uop conversion such as `vfcvt.h.s`, `vftint.w.d`,
`vffint.s.l`, or their LASX forms gives `0x25 = 1` and `0x31 = 2`.
Vector loads/stores and some vector moves are not included: `vld`, `vst`,
`xvld`, `xvst`, and `vreplgr2vr.w` all read zero from `0x25` and `0x31`.
One scalar exception was observed: `movfr2cf` also increments `0x25` and
`0x31` by one. A scan of configs `0x00`-`0xff` did not find separate LSX and
LASX uop counters on LA464, so `0x31` is the combined vector-uop counter.

Scalar floating-point examples for `0x30`: `fadd.d = 1`, `fdiv.d = 1`,
`fsel = 2`, and `fcmp.slt.d = 1`. Most FP moves, FP CSR transfers, and FP
loads/stores do not count: `movgr2fr.d`, `movfr2gr.d`, `movfrh2gr.s`,
`movcf2gr`, `movgr2cf`, `movfcsr2gr`, `movgr2fcsr`, `fld.d`, and `fst.d`
all read zero. The exceptions are `movgr2frh.w = 1` and `movcf2fr = 1`,
while `movfr2cf = 0`. Integer instructions count as zero.

### Unaligned vector memory accesses

On LA464, both the load and store counters split when a vector access crosses
a 64-byte cache line. With a 64-byte-aligned base:

- for a 16-byte LSX access (`vld`/`vst`), `0xa8`/`0xb0` report 2 at offsets
  49-63 and 113-127, and 1 otherwise;
- for a 32-byte LASX access (`xvld`/`xvst`), `0xa8`/`0xb0` report 2 at offsets
  33-63 and 97-127, and 1 otherwise;
- `0x01` stays at 1 per instruction even when the memory counter reports 2.

On LA664, the load/store counters were measured with a 64-byte-aligned base
and unaligned offsets. `0xa8` counts a load twice when the access crosses a
64-byte cache line: for a 16-byte LSX load, offsets such as 49, 56, 63, and
127; for a 32-byte LASX load, offsets such as 33 through 63 and 97 through
127. Loads entirely within one 64-byte line count once. `0xb0` counted one
store for every tested LSX and LASX store offset, including cache-line
crossings, so no store split was visible in that counter on LA664. The
instruction-retired count (`0x01`) remained one per instruction even when
`0xa8` reported two load operations.

The load-splitting behavior therefore matches between LA464 and LA664, but
the store-splitting behavior does not: the LA464 store counter splits on a
64-byte-line crossing, while no LA664 store split was observed.

## Multi-uop instructions

On both microarchitectures, `0x01` counts retired uops, so a multi-uop
instruction has a counter value larger than its static instruction count. The
details of how to separate instructions and uops differ between LA464 and
LA664.

### LA664

In the current LA664 data set, 112 of 1270 instructions with throughput data
are two-uop instructions:

| Category | Count |
| --- | ---: |
| Atomic `am*` | 52 |
| `vfcvt` / `vffint` / `vftint` conversions, LSX and LASX | 48 |
| `vpickve2gr.b/h/w/d`, `xvpickve2gr.w/d` | 6 |
| `llacq.d`, `llacq.w`, `sc.q` | 3 |
| `vreplgr2vr.b/h` | 2 |
| `movgr2frh.w` | 1 |

For these instructions the throughput experiment also records the relevant
extra counters in `result.counters`:

- atomics: `0xa8` (one load) and `0xb0` (one store), even though the atomic
  retires as two uops;
- `llacq.*`: `0xa8` (one load);
- `sc.q`: `0xb0` (one store);
- `vfcvt` / `vffint` / `vftint` LSX: `0xfb` (two LSX uops);
- the same LASX conversions: `0xfa` (two LASX uops);
- `vpickve2gr.*`: `0xfb` (two LSX uops); `xvpickve2gr.w/d`: `0xfa` (two LASX uops);
- `vreplgr2vr.b/h`: `0xfb` (one LSX uop);
- `movgr2frh.w`: `0xfc` (one scalar floating-point uop).

The extra counter values are normalised per instruction (the measured
per-sequence values divided by the eight lanes). They are shown in the
per-instruction throughput table and in the interactive table details.

### LA464

In the current LA464 data set, 41 of 348 instructions with throughput data are
two-uop instructions:

| Category | Count |
| --- | ---: |
| Atomic `am*` | 36 |
| `fsel` | 1 |
| `movgr2frh.w` | 1 |
| `rdtime.d`, `rdtimeh.w`, `rdtimel.w` | 3 |

Unlike LA664, LA464 has a vector-instruction counter (`0x25`) as well as a
vector-uop counter (`0x31`), so the vector relationship can be measured
directly:

```text
vector uops per instruction = 0x31 / 0x25
```

For example, `vadd.b` gives `0x25 = 1` and `0x31 = 1`, while a two-uop vector
conversion gives `0x25 = 1` and `0x31 = 2`. LA464 does not expose separate LSX
and LASX uop counters; `0x31` is the combined LSX+LASX vector-uop count. On
LA464, the `0x31 / 0x25` ratio is therefore the direct way to identify
multi-uop vector instructions.

## CPUCFG and trapped instructions

On LA664, `cpucfg` is a special case for the instruction-retired counter. It is
trapped and emulated: in a 10M-instruction `cpucfg` loop, `instructions:u` is only about
0.13M while the all-mode `instructions` count is about 31M, so roughly 30M
retired instructions happen in the kernel. `bench` excludes kernel events, so
`cpucfg` reports `instructions retired: 0.00`. The validator therefore uses a
retired multiplier of zero for `cpucfg`; its latency and throughput are still
meaningful from the cycle counter.

## Measuring variable-latency instructions

Some instructions take a different number of cycles depending on their
operands: integer divides and remainders, and the floating-point divide, square
root, reciprocal and reciprocal square root units. The resulting models are on
the [Analysis](analysis.md) page; this section is about measuring them.

The problem with such an instruction is that a dependency chain is only usable
if the chain keeps its operand in the same latency class. Three techniques cover
practically everything:

*Pick a fixed point.* If an operation maps a value to itself, a plain chain
measures that class forever. Division by `1` is exact and returns the dividend,
so `div.d $r4, $r4, $r5` with `$r5 = 1` walks the whole quotient ladder by
changing `--init` alone, and `fdiv.d $f0, $f0, $f1` with `$f1 = 1.0` does the
same for floating point. `sqrt(1)`, `rsqrt(1)` and `1.0/1.0` are fixed points as
well, which is why a chain seeded with `1.0` measures the exact class.

*Aim for an oscillation.* A chain that never converges keeps the slow class.
`fdiv.d $f0, $f1, $f0` with `$f1 = 1.0` and a seed of `3.0` bounces between
`0.333...` and `3.0000000000000004`, so every iteration is an inexact quotient.

*Reset through a helper.* When neither works (subnormal or overflowing operands
have no fixed point and no cycle), break the dependency in the integer domain:
`movfr2gr.d $r6, $f0; and $r6, $r6, $r0; or $r6, $r6, $r8; movgr2fr.d $f0, $r6`
restores a constant bit pattern for two cycles of real work. Vector forms use
`xvpickve2gr.*` plus `xvreplgr2vr.*`.

The helper *must* be calibrated, never assumed. Measure the same helper with the
instruction replaced by a cheap known operation (`fadd.d`, `vadd.b`) and subtract
the difference from the known control latency:

```text
latency(op) = cycles(op + helper) - cycles(control + helper) + latency(control)
```

Skipping this has produced wrong values twice in this database: a GPR reset chain
around the vector divider under-reports by two to three cycles because of the
bypass from the divider to the integer pipeline, and an uncalibrated chain
reported `vfdiv.d` of `1.0/1.0` as 8 instead of 10. Sanity-check the calibration
on a case whose latency is already known from a drift-free chain before trusting
the rest of the sweep.

Two more traps specific to this data:

* `li.d $r4, 1; movgr2fr.d $f0, $r4` loads the smallest subnormal, not `1.0`;
  use `0x3ff0000000000000` for double and `0x3f800000` for single precision.
  Vector floats need the same care, and `vreplgr2vr.b` of an integer is not a
  float constant either.
* a vector divide only pays for the lanes that need work, so a body whose lanes
  are partly zero measures below the worst case; build the operand pattern with
  `vinsgr2vr.*` if the number of active lanes matters.

