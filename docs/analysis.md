---
hide:
  - navigation
---
# Analysis

What the measurements say about the LA664 and LA464 cores: the latency models
behind the instructions whose timing depends on their operands. How those
numbers are produced is on the [Methodology](methodology.md) page.

The two cores implement the same models. Where they differ, the difference is
called out next to the numbers; the floating-point units and the vector integer
divider are identical, and the scalar integer divider differs only in how the
remainder path terminates.

## Integer division and remainder

Every integer divide and remainder instruction has a data-dependent latency, and
the data records the measured minimum and maximum instead of a single number.
Both extremes follow from a model that needs only the width of the element and
the number of significant quotient bits, so the ranges were derived from the
model rather than found by brute-force search.

Two properties of the divider explain most of the surprising numbers:

* it divides magnitudes, so the sign of the dividend and divisor never changes
  the timing, only the width of the quotient magnitude;
* it skips trailing zero digits, so a dividend with many low zero bits is much
  cheaper than its bit count suggests.

Both are visible in the data. `div.d` peaks at 38 cycles for
`0x7fffffffffffffff / 1` (a 63-bit quotient) rather than 39, because a signed
quotient magnitude of 64 bits always has its low 63 bits clear and is therefore
cheap, and `-2^63 / 1` measures 8 cycles because 62 of its trailing zeros are
skipped.

### Scalar divider

The scalar divider retires two quotient bits per cycle:

```text
latency = 7 + floor(quotient_bits / 2)
```

so `div.w` tops out at 22 cycles (23 for `div.wu` with a full 32-bit quotient)
and `div.d` at 38 (39 for `div.du`). The minimum is 5 with a zero dividend and a
divisor of 2 or more; a divisor of 1 costs one extra cycle because the quotient
equals the dividend and is therefore never zero.

```text
div.d, dividend = 2^n - 1, divisor = 1
  n=1   7    n=9  11    n=17 15    n=33 23    n=49 31
  n=4   9    n=13 13    n=25 19    n=41 27    n=57 35
  n=8  11    n=15 14    n=31 22    n=48 31    n=63 38
```

Powers of two are much faster than their bit count suggests, because the
quotient is narrow: `0x10 / 1` takes 7 cycles while `0xf / 1` takes 9, and a
dividend with an even number of trailing zeros collapses further, since the
divider normalises two bits per step.

`mod` and `div` are the same instruction unit. On LA664 they measure identically
for every width (5-22, 5-23, 5-38 and 5-39), but on LA464 the remainder path
leaves the long path as soon as the remainder becomes zero, which costs the last
digit:

```text
                 div.w   div.wu   div.d   div.du
LA664            5-22     5-23    5-38     5-39
LA464            5-22     5-23    5-38     5-39
LA664 mod.*      5-22     5-23    5-38     5-39
LA464 mod.*      5-22     5-22    5-38     5-38
```

A wide quotient with a zero remainder only exists for divisor 1 (`max / 1` and
`-1 / 1`), so the divide reaches 23 and 39 through that case while the remainder
never does on LA464. Both cores agree on `mod.w` and `mod.d`, where a 31-bit or
63-bit quotient is wide enough with a non-zero remainder.

### Vector divider

The vector divider is much wider per quotient bit but has a larger fixed cost,
which is why its range is *narrower* than the scalar one even though the vector
instruction divides several times as many bits:

```text
latency = 5 + (64 / width) * (6 + floor(quotient_bits / 4))
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

Two more properties show up in the measurements:

* the lanes are processed in parallel. LSX and LASX are identical for every
  element width although LASX carries twice as many lanes, and the low and high
  128-bit halves are not serialised either:

  ```text
  quotient        LSX vdiv      LASX xvdiv
  0x7fffffffffffffff  25.93        25.99     (.d)
  0x7fffffff          30.99        30.98     (.w)
  0x7fff              40.99        40.93     (.h)
  0x7f                60.97        60.97     (.b)
  ```

* only the element width sets the per-bit rate: the divider behaves like one
  256-bit datapath that retires 16 quotient bits per cycle in aggregate, 4 bits
  per lane for `.d`, 2 for `.w`, 1 for `.h` and one bit every other cycle for
  `.b`.

Like the scalar divider, the vector unit divides magnitudes only and skips
trailing zero digits: `2^40 / 1` (a 41-bit quotient) still measures 11 cycles
because the 40 trailing zeros cost nothing.

LA464 and LA664 agree on every vector integer number: 29-61 for `.b`, 17-41 for
`.h`, 11-31 for `.w` and 8-26 for `.d`, with `mod` one digit behind (29-69,
17-45, 11-33 and 8-27), and both cores give LSX and LASX the same range.

`mod` costs one extra digit, because the remainder needs the last digit that the
quotient path can discard, which adds 1, 2, 4 or 8 cycles depending on the
element width: 27/33/45/69 signed and 28/35/49/77 unsigned.

### Idle lanes

A vector divide only pays for the lanes that need work. With divisor 1, which is
a fixed point for the measured value, and a number of active lanes:

```text
             1 lane   2 lanes  4 lanes  8 lanes  16 lanes
vdiv.bu        34       39       49       69        69
vdiv.hu        24       31       45       45        —
vdiv.wu        22       33       33       —         —
vdiv.du        27       26       —        —         —
```

The saving saturates once 64 bits of dividend are active, and the extra lanes
beyond that are free. A vector whose bytes are only partly non-zero therefore
measures well below the maximum recorded in the data, which assumes every lane
is active.

## Floating-point division, square root and reciprocal

The five floating-point units of interest here share one mechanism, and LA464
reproduces every class below exactly: `fdiv`, `fsqrt`, `frecip` and `frsqrt` are
identical on the two cores, as are their vector forms. A result is
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
* a subnormal operand costs one extra digit time, while a tiny inexact *result*
  rather than a tiny operand saves one cycle instead.

Each class has a reproducible recipe, and the JSON stores it with the class (the
`cases` array carries the `init`, the `asm` and the full `bench` command):

| class | recipe |
| --- | --- |
| `0`, `Inf`, `NaN` input | dividend or input `0`, divisor `1.0`; a fixed point |
| overflow or underflow | dividend `max`, divisor `minnorm`; needs the reset helper, because the quotient is `Inf` and a chain would then measure the `Inf` input class |
| exact | `1.0/1.0`, `sqrt(1.0)`, `rsqrt(1.0)`, `1/1.0`; all fixed points |
| exact, subnormal operand | `maxsub/maxsub`, `sqrt(2^-1024)`, `1/2^-1023`: a subnormal whose result is exact |
| inexact | divisor chain started at `3.0`, `sqrt(2.0)`, `rsqrt(2.0)`, `1/3.0`; needs the helper except for the divisor chain |
| inexact, subnormal operand | `1.0/maxsub` (division) or `sqrt(maxsub)`, `rsqrt(maxsub)`, `1/maxsub` |
| tiny inexact result (single precision) | `1.0/max` |
| just above an exact power of two (`rsqrt`) | `(1+2^-52)/1` or `(1+2^-23)/1` |

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
`latency(single-precision vector) = 2*latency(single-precision scalar) - 5`, and
the five saved cycles are the setup and rounding steps that the two datapath
passes share.

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
datapath passes that the divider also makes for division, giving
`2*22 - 5 = 39`.

### Reciprocal square root

`frsqrt` costs three digit times, so the long path is `15 + 3*13 = 54` and
`15 + 3*6 = 33`. Its exact cases are again the even powers of two, and it has an
extra intermediate class for inputs just above an exact power of two
(`1 + 2^-52`), where the result needs a few more digits than the exact case.

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
vfrsqrt.s intermediate class (input just above an exact power of two): 49
```

The single-precision vector form measures `2*33 - 5 = 61` for the general case,
the same rule as the other units.

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

## Load latency

Loads are not data-dependent, but their latency still depends on two things:
which operand closes the chain (the base address, or the index and the bound)
and what consumes the result (an ALU operation, or the next address). The data
records one entry per source operand, and the notes name the bypass path.

### Plain loads

On LA664 every plain load takes 4 cycles to an ALU consumer and 3 cycles when
the result feeds the next address, the load-to-AGU bypass:

| load | LA664 (ALU / bypass) | LA464 (ALU / bypass) |
| --- | --- | --- |
| `ld.b`, `ld.h`, `ld.w`, `ld.d`, `ldptr.*`, `ld.bu`, `ld.hu`, `ld.wu` | 4 / 3 | 5 / 4 |
| `fld.s` | 4 / 3 | 5 / 4 |
| `fld.d` | 4 / 3 | 4 / 3 |
| `vld`, `xvld` | 4 / 3 | 5 / 4 |

Width does not matter on LA664, a byte load costs exactly what a double load
costs, and an integer, floating-point or vector load all take the same time. The
recorded `cycles` value is the ALU path.

LA464 needs one cycle more on both paths: 5 to an ALU consumer and 4 through the
bypass, which is the main load-latency difference between the two cores. The one
disagreement inside LA464 is `fld.d`, recorded as 4 while `fld.s` is 5; a double
load should not be faster than a single one, so that entry needs re-measuring.

### Indexed and bound-checked loads

The indexed and bound-checked forms have more than one real path, because the
index or the bound takes part in the address calculation. On LA664:

| path | cycles |
| --- | --- |
| base operand (`rj`) to an ALU consumer | 4 |
| base operand used as the next address (bypass) | 3 |
| index operand (`rk`) | 5 |
| bound operand (`rk`) to an ALU consumer | 5 |
| bound operand fed straight back as the next bound | 4 |

The extra cycle on the index and bound paths is the second address-generation
pass. That the bound has two values depending on its consumer is easy to see in
`ldle.d`: feeding the loaded value back as the bound for the next load measures
3.99 cycles, while putting an `add.d` or `xori` between the load and the next
bound measures 5. A floating-point bound check always needs the integer ALU to
move the value out of the register file, which is why `fldle.*` and `fldgt.*`
both record 5 on the bound path and only the integer `ldle.*` shows the 4-cycle
bypass.

The recorded `cycles` for these instructions is the ALU path, so
`ldx.*`/`ldgt.*`/`fldx.*`/`fldgt.*`/`fldle.*` read 4 for the base operand and 5
for the index or bound, while the integer `ldle.*` records 4 twice because its
measured bound chain is the bypass one.

LA464 records the indexed and bound forms with one combined `from_operands:
[2, 3]` entry of 5 cycles, so its per-source split has not been measured and the
two cores cannot be compared operand by operand yet. Since LA464 pays one extra
cycle on the base path, its index and bound paths are probably 6, but that is a
prediction and not a measurement.

### What does not change the latency

All of these numbers are L1D hits with a hot, 64-byte-aligned buffer. A vector
access that crosses a cache line is counted as two operations by the load
counter `0xa8` (see the [Methodology](methodology.md) page), but the latency
data was taken with aligned buffers, so unaligned latency is unmeasured rather
than measured-and-equal.

## Summary

| instruction | range (cycles) | long-path cost |
| --- | --- | --- |
| `div.w`/`mod.w` | 5-22 | 7 + 2 quotient bits per cycle |
| `div.wu`/`mod.wu` | 5-23 | same |
| `div.d`/`mod.d` | 5-38 | same |
| `div.du`/`mod.du` | 5-39 | same |
| `vdiv.b`/`vmod.b` | 29-61 / 29-69 | 16 cycles of digit work, one digit = 8 cycles |
| `vdiv.bu`/`vmod.bu` | 29-69 / 29-77 | same |
| `vdiv.h`/`vmod.h` | 17-41 / 17-45 | one digit = 4 cycles |
| `vdiv.hu`/`vmod.hu` | 17-45 / 17-49 | same |
| `vdiv.w`/`vmod.w` | 11-31 / 11-33 | one digit = 2 cycles |
| `vdiv.wu`/`vmod.wu` | 11-33 / 11-35 | same |
| `vdiv.d`/`vmod.d` | 8-26 / 8-27 | one digit = 1 cycle |
| `vdiv.du`/`vmod.du` | 8-27 / 8-28 | same |
| `fdiv.s` | 8-17 | 10 + 1 digit time (6) |
| `fdiv.d` | 8-24 | 10 + 1 digit time (13) |
| `vfdiv.s` | 11-29 | 15 + 2 digit times |
| `vfdiv.d` | 8-24 | identical to `fdiv.d` |
| `fsqrt.s` | 8-23 | 10 + 2 digit times |
| `fsqrt.d` | 8-37 | same |
| `vfsqrt.s` | 11-41 | 15 + 4 digit times |
| `vfsqrt.d` | 8-37 | identical to `fsqrt.d` |
| `frsqrt.s` | 11-34 | 15 + 3 digit times |
| `frsqrt.d` | 11-55 | same |
| `vfrsqrt.s` | 17-63 | 25 + 6 digit times |
| `vfrsqrt.d` | 11-55 | identical to `frsqrt.d` |
| `frecip.s` | 8-17 | 10 + 1 digit time |
| `frecip.d` | 8-24 | same |
| `vfrecip.s` | 11-29 | 15 + 2 digit times |
| `vfrecip.d` | 8-24 | identical to `frecip.d` |

Every entry in this table holds for both cores except `mod.wu` (5-22 on LA464,
5-23 on LA664) and `mod.du` (5-38 against 5-39), the only two numbers that the
two microarchitectures do not share.

Load latency does not depend on the data, only on the path:

| load | LA664 | LA464 |
| --- | --- | --- |
| plain `ld.*`, `fld.*`, `vld`, `xvld` to an ALU consumer | 4 | 5 |
| plain load feeding the next address | 3 | 4 |
| `ldx.*`, `ldgt.*`, `fldx.*`, `fldgt.*`, `fldle.*` base / index or bound | 4 / 5 | 5, split unmeasured |
| integer `ldle.*` bound fed back as the next bound | 4 | 5, split unmeasured |

The floating-point classes are a fixed set that covers every possible operand,
so the JSON lists them as an exhaustive `cases` array, and no formula is needed
on the instruction page. The integer ladders are continuous: their `cases`
entries are only the named special cases (zero dividend, one-digit quotient,
full-width quotient) and the notes carry the formula for everything in between.
Every case, in both families, carries the operand setup and the `bench` command
that provokes it, so any number on a page can be reproduced by copy and paste.
Cases that need a helper instruction also record its calibrated cost as
`chain_latency`.
