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

## Integer divide latency that depends on the operands

Every integer divide and remainder instruction on LA664 has a data-dependent
latency, and the data in `docs/data` records the measured minimum and maximum
instead of a single number. Both extremes follow from a model that only needs
the width of the element and the number of significant quotient bits, so the
ranges below were derived rather than found by brute-force search.

A chain that holds its operand constant is enough to verify the model: with a
divisor of 1 the quotient equals the dividend, so `div.d $r4, $r4, $r5` feeds
exactly the same value back into the next iteration and stays in the same
latency class. No helper instruction and no operand sweep are needed; setting
`--init` to a different dividend walks the ladder one rung at a time.

The divider works on the magnitudes of its operands, so the sign of the dividend
and divisor never changes the timing; only the width of the quotient magnitude
matters, and the divider skips trailing zero digits. Both effects are visible in
the data: `div.d` peaks at 38 cycles for `0x7fffffffffffffff / 1` (a 63-bit
quotient) rather than 39, because a signed quotient magnitude of 64 bits always
has its low 63 bits clear and is therefore cheap, and `-2^63 / 1` measures 8
cycles because 62 of its trailing zeros are skipped.

### Scalar divider

The scalar divider retires two quotient bits per cycle:

```text
latency = 7 + floor(quotient_bits / 2)
```

so `div.w` tops out at 22 cycles (23 for `div.wu` with a full 32-bit quotient)
and `div.d` at 38 (39 for `div.du`). The minimum is 5 with a zero dividend and
a divisor of 2 or more; a divisor of 1 costs one extra cycle because the
quotient equals the dividend and is therefore never zero. LA464 and LA664 are
identical here: both measure 5-22, 5-23, 5-38 and 5-39, so the two cores share
the same scalar divider.

```text
div.d, dividend = 2^n - 1, divisor = 1
  n=1   7    n=9  11    n=17 15    n=33 23    n=49 31
  n=4   9    n=13 13    n=25 19    n=41 27    n=57 35
  n=8  11    n=15 14    n=31 22    n=48 31    n=63 38
```

Powers of two are much faster than their bit count suggests, because the
quotient is narrow: `0x10 / 1` takes 7 cycles while `0xf / 1` takes 9, and a
dividend with an even number of trailing zeros collapses further (the divider
normalises two bits per step).

### Vector divider

The vector divider is much wider per quotient bit but has a larger fixed cost,
which is why the LASX range is *narrower* than the scalar one even though the
vector instruction divides four times as many bits:

```text
latency = 5 + 6 * (64 / width) + 16 * quotient_bits / width
```

`width` is the element width in bits. This reduces to

| elements | multiplier of `floor(bits/4)` | minimum | maximum (signed / unsigned) |
| --- | --- | --- | --- |
| `.d` (64-bit) | 11 + 1 × | 8 | 26 / 27 |
| `.w` (32-bit) | 17 + 2 × | 11 | 31 / 33 |
| `.h` (16-bit) | 29 + 4 × | 17 | 41 / 45 |
| `.b` (8-bit) | 53 + 8 × | 29 | 61 / 69 |

The minimum is always the zero-dividend case and does not depend on the divisor.
A full-width quotient always costs 16 cycles of refinement on top of the base,
whatever the element width, so an 8-bit vector divide is almost three times
slower than a 64-bit one: `xvdiv.bu` takes 69 cycles against 39 for `div.du`.

Two properties of the unit are visible in the measurements:

* The lanes are processed in parallel, not one after another. On LA664 the LSX
  and LASX forms are identical for every element width although LASX carries
  twice as many lanes, and the low and high 128-bit halves are not serialised
  either.

  ```text
  quotient        LSX vdiv      LASX xvdiv
  0x7fffffffffffffff  25.93        25.99     (.d)
  0x7fffffff          30.99        30.98     (.w)
  0x7fff              40.99        40.93     (.h)
  0x7f                60.97        60.97     (.b)
  ```

* Only the element width sets the per-bit rate: the divider behaves like one
  256-bit datapath that retires 16 quotient bits per cycle in aggregate
  (4 bits per lane for `.d`, 2 for `.w`, 1 for `.h` and one bit every other
  cycle for `.b`).

Like the scalar divider, the vector unit divides magnitudes only, and it skips
trailing zero digits: `2^40 / 1` (a 41-bit quotient) still measures 11 cycles
because the 40 trailing zeros cost nothing.

LA464 matches these numbers for every element width and, like LA664, gives the
same range for the LSX and LASX forms (8-26, 11-31, 17-41 and 29-61 for `.d`,
`.w`, `.h` and `.b`), so both cores share the same vector integer divider.

## Floating-point division, square root and reciprocal latency

All five floating-point units of interest here share one mechanism. A result is
produced one four-bit "digit" of the mantissa at a time and the unit stops as
soon as the remainder is zero, so latency is a small set of classes rather than a
ladder:

```text
D = ceil(mantissa / 4)          D = 13 for double (52 bits), 6 for single (23 bits)

fdiv, frecip    base + 1*D      exact -> no digit at all
fsqrt           base + 2*D
frsqrt          base + 3*D
```

"Exact" means the result terminates in binary (`1.0/2.0`, `1.5/1.0`, `3.0/4.0`
for division and reciprocals; an even power of two for `sqrt` and `rsqrt`).
Three further effects shift the numbers:

* an input of `0`, `Inf` or `NaN` short-circuits the unit;
* a quotient or root that overflows to `Inf` or underflows to zero is detected
  early and is *cheaper* than a normal inexact result;
* a subnormal operand costs one extra digit time, and a tiny inexact result
  rather than a tiny operand saves one cycle instead.

### Division

#### Scalar

```text
                0/Inf/NaN  ovf/unf  exact  exact+subnormal  inexact  inexact+subnormal
fdiv.s              8        9       10          11           16          17
fdiv.d              8        9       10          11           23          24
```

The long path is `10 + ceil(52/4) = 23` for double and `10 + ceil(23/4) = 16`
for single, which means four quotient bits per cycle. `1.0/max` (a tiny inexact
result) measures 15 rather than 16 in single precision, and `minnorm/3` behaves
the same way.

#### Vector

`vfdiv.d` and `xvfdiv.d` reproduce the scalar numbers exactly, lane count
included, because the vector unit matches the scalar divider for 64-bit
elements. Single precision runs at half rate:

```text
                        0/Inf/NaN  ovf/unf  exact  exact+subnormal  inexact  inexact+subnormal
vfdiv.s, xvfdiv.s           11      13      15          17           27          29
```

so the vector single-precision divide is *slower* than the scalar one (27
against 16) while both doubles are identical. Every class obeys
`latency(32-bit vector) = 2*latency(32-bit scalar) - 5`, and the five saved
cycles are the setup and rounding steps the two datapath passes share.

### Square root

`fsqrt` costs two digit times, so the long path is `10 + 2*13 = 36` for double
and `10 + 2*6 = 22` for single. There is no overflow class because a square root
cannot overflow, and an exact result requires an even power of two as input.

#### Scalar

```text
                0/Inf/NaN  exact  exact+subnormal  inexact  inexact+subnormal
fsqrt.s             8       10          11           22          23
fsqrt.d             8       10          11           36          37
```

`sqrt(minnorm)` is exact and measures 10, while `sqrt(maxsub)` measures 37
because the input is subnormal.

#### Vector

```text
                        0/Inf/NaN  exact  exact+subnormal  inexact  inexact+subnormal
vfsqrt.d, xvfsqrt.d         8       10          11           36          37
vfsqrt.s, xvfsqrt.s        11       15          17           39          41
```

The 64-bit lanes reproduce the scalar numbers; single precision makes the two
datapath passes the divider also makes for division, giving `2*22 - 5 = 39`.

### Reciprocal square root

`frsqrt` costs three digit times, so the long path is `15 + 3*13 = 54` and
`15 + 3*6 = 33`. Its exact cases are again the even powers of two, and it has an
extra intermediate class for inputs just above an exact power of two
(`1 + 2^-52`), where the result needs one more digit than the exact case.

#### Scalar

```text
                0/Inf/NaN  exact  exact+subnormal  just above exact  inexact  inexact+subnormal
frsqrt.s            11      15          16               27            33          34
frsqrt.d            11      15          16               41            54          55
```

#### Vector

```text
                        0/Inf/NaN  exact  exact+subnormal  inexact  inexact+subnormal
vfrsqrt.d, xvfrsqrt.d       11      15          16           54          55
vfrsqrt.s, xvfrsqrt.s       17      25          27           61          63
```

The single-precision vector form measures `2*33 - 5 = 61` for the general case,
the same `2*x - 5` rule as the other units.

### Reciprocal

`frecip` has a single operand and the same digit time as division, so it repeats
the division numbers apart from the paths that involve the second operand.

#### Scalar

```text
                0/Inf/NaN  exact  exact+subnormal  tiny inexact  inexact  inexact+subnormal
frecip.s            8       10          11             15          16          17
frecip.d            8       10          11             —           23          24
```

An exact reciprocal requires a power of two; the subnormal case is `1/maxsub`,
which measures 24 in double precision.

#### Vector

```text
                        0/Inf/NaN  exact  exact+subnormal  tiny inexact  inexact  inexact+subnormal
vfrecip.d, xvfrecip.d       8       10          11             —           23          24
vfrecip.s, xvfrecip.s      11       15          17             25          27          29
```

### Building the chains

Division and reciprocals can be measured without any helper instruction, which
is why their numbers are the most reliable in this database:

* divisor `1.0` makes the quotient identical to the dividend, so
  `fdiv.d $f0, $f0, $f1` is a stable chain; a dividend with a low-order mantissa
  bit set selects the long path;
* a chain started at `3.0` on the divisor side (`fdiv.d $f0, $f1, $f0` with
  `$f1 = 1.0`) oscillates between `0.333...` and `3.0000000000000004` and never
  terminates, which selects the long path directly;
* `fsqrt` and `frsqrt` converge to their own fixed points instead (`sqrt(1) = 1`,
  `rsqrt(1) = 1`), so a chain seeded with `1.0` measures the exact class and the
  inexact classes need the reset helper;
* subnormal, overflow and underflow cases cannot chain at all, so they need a
  reset helper (`movfr2gr.d` + `and` + `or` + `movgr2fr.d`), and its cost must be
  calibrated against a `fadd.d` carried through the same helper. The vector reset
  helper costs two to three cycles more than the instruction count suggests
  because of the bypass from the divider to the integer pipeline, so calibrating
  it on a known case (`vfdiv.d` of `1.0/1.0` is 10 cycles) is mandatory.

Do not reuse the operand bits of an integer as a floating-point constant:
`li.d $r4, 1; movgr2fr.d $f0, $r4` loads the smallest subnormal, not `1.0`.

### Cross-core comparison

The separate LA464 campaign reports the same class structure and the same
numbers for division (`9`, `15`, `17` and `24` cycles appear there too), so the
floating-point divider did not change between the cores. The only known
difference is in the 128-bit integer divide of 64-bit elements, where LA464
needs one extra cycle (`vdiv.d` 9-27 against `xvdiv.d` 8-26) while LA664 measures
8-26 for both widths.
