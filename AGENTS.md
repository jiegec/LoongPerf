# AGENTS.md — measuring instructions for LoongPerf

This file explains how to add or re-measure instruction data, and which traps
have already cost us time. Read it before touching `docs/data/`.

## Repository layout

```text
bench.cpp, Makefile        the measurement harness (`make` builds ./bench)
schema/instruction.schema.json   JSON schema for one instruction on one uarch
docs/data/<uarch>/<insn>.json    one file per instruction per microarchitecture
docs/methodology.md              how to measure, and the traps
docs/analysis.md                 the resulting latency models
tools/validate_data.py           schema + semantic validator (must pass)
tools/build_site.py              generates the per-instruction pages from the JSON
mkdocs.yml, main.py              Zensical configuration and hooks
```

Data lives under `docs/data/la664` (Loongson-3C6000, LA664) and
`docs/data/la464` (Loongson-3C5000, LA464). The microarchitecture is encoded in
the path, never in the JSON body.

## Environment checks before measuring

```sh
uname -m                                  # must be loongarch64
lscpu | grep 'Model name'                 # Loongson-3C6000/S is LA664
cat /proc/sys/kernel/perf_event_paranoid  # 2 works when running as root
make -j"$(nproc)"
```

`bench` needs `perf_event_open(2)`; run it as root or lower
`perf_event_paranoid`. Pin to one core with `--core` and keep that core for a
whole batch. `--reps` takes the **minimum** over repetitions, so a single fast
run can win; use `--reps 20` and repeat the whole command a few times when a
value matters. This machine has no SMT (`thread_siblings_list` is each CPU
itself) but the 32 cores share one 16 MiB L3, so other users' work still shows up
as a few percent of drift — always re-check the control measurements above
before trusting a batch. Fix the frequency situation (this machine exposes no cpufreq
governor and no SMT: `thread_siblings_list` is each CPU itself).

Known-good control measurements on LA664 — if these drift, the machine is busy
and every number in the batch is suspect:

```text
one dependent add.d                 1.00 cycle
4 dependent add.d                   4.00 cycles
8 independent add.d (4 IPC)         1.98-2.00 cycles  (0.2475 CPI)
fadd.d chain                        3.00 cycles
lu32i.d 8 lanes (4 IPC)             4.00 cycles
```

## The measurement model

`./bench` compiles the `--asm` sequence into a shared object and calls it
`--iters` times, counting cycles and instructions with `perf_event_open(2)`.

Every call pays a fixed cost `K` (call/ret, the register-save prologue and
epilogue, the caller's loop) that the body partially hides. Empirically

```text
raw(U) = c - K/U        with K ≈ 75-80 cycles per call on LA664
```

so a single body measurement is biased low at small unroll and converges from
below: `fadd.d` reads 2.23 at `--unroll 100`, 2.86 at 1000, 2.99 at 10000.

**Do not subtract an empty-function baseline.** An empty body cannot overlap its
prologue with anything, so it measures a *different* `K` than a real body does;
the old code did this and produced values like 0.98 for a 1-cycle instruction.

Use the default `--method slope`: two bodies (`--unroll N` and `--unroll2 N2`,
default `2N`) with an identical number of calls are measured and differenced, so
both `K` and the per-measurement cost cancel exactly. `--method raw` reports one
body unchanged and is only useful for comparing runs made with identical
settings, or for self-timed loops where doubling the body would change the
program's semantics.

## Sizing the body: the L1I trap

Each core has **64 KiB of L1I**. A body of `instrs × unroll` instructions is
`instrs × unroll × 4` bytes, and the slope method runs *two* such bodies, so the
larger one is `instrs × unroll2 × 4` bytes.

```text
8 instructions × unroll 1000 / unroll2 2000  → 32 KiB / 64 KiB  ← at the limit
8 instructions × unroll  250 / unroll2 1000  →  8 KiB / 32 KiB  ← safe
```

Exceeding L1I inflates CPI by 5–30% and is the reason many early throughput
numbers were slightly high (vector ALU ops read 0.53 CPI instead of 0.50,
`rotr.b`/`rcr.*` read 1.08–1.10 instead of 1.00). For latency chains a single
short sequence repeated is safe at large unroll because the dependency chain,
not fetch, is the limit — but keep throughput bodies small.

Recommended defaults:

| what | unroll | unroll2 | iters |
|---|---|---|---|
| latency chain (1–4 instructions) | 1000 | 4000 | 1000 |
| throughput, 8 lanes | 250 | 1000 | 1000 |
| throughput, tiny CPI (< 0.5) | 250 | 1000 | 1000 |
| branch loop (self-timed) | 1 | — | 100 |

with `--reps 20` everywhere. Latency chains also work at very large unroll
(100000) because the dependency chain, not fetch, limits them; throughput bodies
must stay inside L1I.

## Branch instructions: the BTB trap

Taken-branch throughput depends on how many *static* branch instructions exist,
because each static branch needs a BTB entry:

```text
b 4 (taken to next instruction), LA664
  8 static branches    1.00 CPI
  256 static branches  2.86 CPI
  1024+ static branches 3.01 CPI
```

So a taken-branch number is only meaningful together with a static footprint.
`--method slope` is unusable here (it doubles the static count), and so is a
large `--unroll`. The working protocol is a self-timed loop with a small static
footprint and a matched baseline:

```asm
.p2align 4
.Lloop:
    <N copies of the branch>      # N <= 8
    addi.d $r12, $r12, -1
    bnez   $r12, .Lloop
```

with `--init '... ; li.d $r12, 1000'`, `--method raw --unroll 1 --iters 100`,
and compare against the same loop with the branches replaced by `nop`s.
Measured on LA664: taken = 1 branch/cycle, integer not-taken = 2/cycle,
FCC (`bceqz`/`bcnez`) not-taken = 1/cycle, and taken branches degrade to
3 cycles once the static stream exceeds the BTB. When the loop branch shares a
slot with the measured branches the marginal looks smaller than the asymptote
(0.437 at 8 static branches, converging to 0.5) — record the asymptote, not the
small-N marginal.

`jirl` uses the same loop protocol and retires at one taken branch per cycle:
a loop with 8 static JIRLs plus the loop branch executes 9 taken branches in
9.00 cycles (9009.91 cycles per 1000 iterations, reproduced within 0.1%), and
the same loop with 1 or 2 static JIRLs gives 1.00 cycles each. An earlier
1.279 CPI value in the data could not be reproduced with any baseline we tried
and was replaced.

## Latency chains

Latency is measured with a chain that closes through the measured operand:

```asm
# rj -> rd
add.d $r12, $r12, $r13

# rk -> rd: two alternating copies, divide the result by 2
rotr.b $r13, $r12, $r13 ; rotr.b $r12, $r13, $r12

# flags -> rd, closed through x86mtflag (3 cycles) or armmtflag
adc.b $r12, $r13, $r14 ; x86mtflag $r12, 1

# rd -> flags, closed through x86mfflag (1 cycle)
x86add.b $r12, $r13 ; x86mfflag $r12, 1
```

Rules and pitfalls:

- Record `chain_latency` and let the validator re-derive `cycles`; never hand
  edit `cycles` without updating `experiments[].result.core_cycles`.
- **Some latencies are not additive.** Reusing an expensive closure (for example
  `vpickve2gr.d`, 6 cycles) to chain a vector load under-reports the load,
  because the pick can bypass the load result. Prefer a closure built from
  cheap, independently measured moves, and cross-check with a second closure
  (`vld -> vst -> ld.d -> add.d`, `movcf2gr` vs `movcf2fr`, ...).
- Flag paths are asymmetric: `rd -> EFLAGS` is 3 cycles, `EFLAGS -> rd` is
  1 cycle (LA664). Calibrate both directions before measuring an instruction
  that touches flags.
- Some instructions have several independent paths (`rj`, `rk`, `rd` as
  accumulator, flags). Record one entry per source, as uops.info does. A combined
  `from_operands: [2, 3]` entry is only acceptable when the sources are tied
  together by construction, and even then prefer splitting.
- Memory operands: indexed and bound-checked loads have **two** paths. On LA664
  the base/address path is 4 cycles and the index/bound path is 5 cycles for
  `ldx.*`, `ldgt.*`, `fldgt.*`, `fldx.*`, `fldle.*`. Address chases drift out of
  the buffer if the loaded value is not made to alternate, so store a pair of
  `+8 / -8` words and let the address alternate; for bound paths keep the memory
  word small so the check keeps passing.
- Byte/half unsigned loads (`ld.bu`, `ld.hu`, `ld.wu`) cannot close an address
  loop directly; use `ext.w.b`/`ext.w.h` (1 cycle) rather than two shifts.
- `ld.d` and other 64-bit loads are **4 cycles** to an ALU consumer but **3
  cycles** on the load-to-AGU address bypass. Both numbers are real; record the
  ALU path as the latency and mention the bypass in `notes`.
- `cpucfg` traps to the kernel: the user-mode instruction counter reads 0 and the
  values bounce between 11 and 16 cycles. Record it with a note, and keep the
  validator's retired multiplier at 0 for it.
- Variable-latency instructions (`div`, `mod`, `fdiv`, `fsqrt`, `frecip`,
  `frsqrt`, `vdiv`, `vmod`, and the vector float variants) need an operand sweep
  and `cycles_min`/`cycles_max`; the single sample in `cycles` must sit inside
  that range. Data-dependent behaviour on LA664, for reference:
  `fdiv.d` 8–24, `fsqrt.d` 8–37, `frsqrt.d` 11–55, `div.d` 5–38.
- `ll.w`/`ll.d` alternate 9 and 10 cycles because of the monitor bookkeeping, so
  the average 9.5 is recorded with a 9–10 range; `llacq.*` is 20 (20–21).

## Throughput

Eight independent lanes, each writing a different destination register.

**The lanes must be genuinely independent.** A lane whose `rd` is another lane's
`rk` (or an accumulator) creates a dependency and inflates CPI. This alone
produced a wrong 1.10 CPI for `adc.b`; with disjoint registers the same body
measures exactly 1.00. Use `$r4..$r11` for one operand and `$r12..$r20` or
`$r22..$r29` for the other.

Register rules inside `--asm` and `--init`:

- `$r3` is `$sp` and `$r2` is `$tp`; clobbering either one crashes the process
  immediately (a `$r3` write produced a silent hang in one experiment).
- The generated body saves and restores `$r1` and `$r22..$r31` around the body,
  so those are safe to use but their values do not survive across calls.
- `$r4..$r20` are scratch; `$r21` is reserved by the ABI, so avoid it.
- `init` runs once per call, before the unrolled body, so values it sets are
  visible to every copy of the body.

Other rules:

- Flags operations read one (renamed) flag register; that is fine, but
  `x86mtflag`/`armmtflag` must be re-executed in `--init` and its cost is outside
  the loop.
- Verify the micro-architectural ceiling with a control: `or`/`add.d`/`addi.d`/
  `lu32i.d` all reach exactly 4 IPC, `lu12i.w` reaches 24/5 = 4.8 IPC (verified
  from 4 to 32 lanes), `pcaddi` only 2.4 IPC. A value above 4 IPC that is not one
  of these is suspicious.
- Atomics, LL/SC and any memory instruction depend on cache state: keep the
  buffer small, hot and **64-byte aligned**. On LA664 a vector load that crosses
  a 64-byte line is counted twice by the load counter `0xa8` (an LSX 16-byte load
  at offsets 49/56/63, a LASX 32-byte load at 33-63 and 97-127), while the store
  counter `0xb0` did not split in any offset we tried and the retired-uop count
  stayed at one. `am*` throughput is 13 CPI (16 for `_db`) whether the eight
  lanes hit the same line or eight different lines.
- Streaming stores (`vst*`, `xvst*`, `st*`, `fst*`) are 0.5 CPI, LASX included,
  once the buffer is aligned. Unaligned 32-byte LASX stores degrade: `xvst`
  measured 16.02 cycles for 8 stores (2.0 CPI) and `xvstx` 20.29 cycles
  (2.5 CPI) with an unaligned buffer, versus 4.00 cycles when aligned.

## Multi-uop instructions

`perf`'s instruction event counts **retired uops** on these cores, not
architectural instructions. `vpickve2gr.*`, `vreplgr2vr.b/h`, `movgr2frh.w`,
`vfcvt.*`, `vffint.*`, `vftint.*`, `sc.q`, `llacq.*` and every `am*` atomic
retire two uops, so
`instructions_retired` is twice the static count. When adding such an
instruction, add it to `RETIRED_MULTIPLIER` in `tools/validate_data.py` (the
validator compares against the static instruction count).

Extra raw counters are available with `--counter`:

| config | meaning |
| --- | --- |
| `0x01` | retired uops |
| `0xa8` / `0xb0` | load / store operations (per instruction, not per uop) |
| `0xfb` / `0xfa` / `0xfc` | LSX / LASX / scalar-FP uops (LA664 only) |

LA464 uses a different map for the vector counters: `0x25` counts vector
instructions, `0x31` vector uops and `0x30` scalar-FP uops; `0x01`, `0xa8` and
`0xb0` mean the same on both cores. At most two extra counters fit (four PMU
counters per core).

## JSON conventions

- `form` lists **only** the syntactic operands, in instruction order:
  `adc.b rd, rj, rk`, not `adc.b rd, rj, rk, CF`.
- Implicit operands (`CF`, `EFLAGS`, `ARMFLAGS`) stay in `operands` with
  `"implicit": true` and must not appear in `form`.
- Operand names must equal the `form` tokens positionally (`si12`, `uimm`, ...).
- The same instruction must use the same `form` on both microarchitectures.
- Every entry needs a `notes` explaining how the value was obtained, including
  deviations from nominal integers and any measurement that was not possible.
- Prefer whole-cycle values and round measurement noise (3.97 -> 4, 1.99 -> 2).
  Genuine averages keep their fraction together with a range: `ll.w`/`ll.d` are
  9.5 cycles with `cycles_min` 9 and `cycles_max` 10 because consecutive LLs
  alternate, and LA464 `x86mul.wu`/`hu` hold 1.23 from the vendor campaign.
- A data-dependent latency lists its special cases in `cases`
  (`{"name": ..., "cycles": ...}`) and `cycles_min`/`cycles_max` must equal the
  smallest and largest case. If those cases cover every possible operand, as
  they do for the floating-point divide, square root and reciprocal units, no
  formula is needed and each case carries an `experiment` with the operand setup
  and the `bench` command that provokes it. If they do not, as for integer
  division where the latency is a continuous ladder, the `notes` must also give
  the formula that covers the remaining inputs and the cases are only named
  sample points.
- Keep the arithmetic consistent: `cpi == 1/ipc` within rounding,
  `cycles == (core_cycles - chain_latency) / cycles_divisor` and
  `cpi == (core_cycles - baseline_cycles) / lanes`.
- Do not add `machine_code` by hand; it is optional metadata.

## Validation workflow

```sh
uv run python tools/validate_data.py        # must print OK, exit status 0
rm -rf __pycache__ .cache site && .venv/bin/zensical build --clean --strict
```

The validator also checks operand/form consistency, implicit flag operands,
cross-uarch `form` equality and the retired-instruction multipliers. If it
fails, fix the data — never relax the check.

**Do not pipe the validator through `tail`/`head` when the exit status matters**
(`set -o pipefail`, or run it plainly). Two batches were committed with failing
data because `uv run ... | tail -2` returned the pipeline's status instead of the
validator's.

When a merge conflicts on `docs/data/**`, resolve per instruction and re-run the
validator; a conflict marker inside JSON only shows up as a `JSONDecodeError`
from the cross-uarch check, far away from the real problem.

## Instructions that cannot be measured

Privileged instructions trap in user mode (`csrrd`, `csrwr`, `csrxchg`,
`iocsrrd/wr.*`, `cacop`, `lddir`, `ldpte`, `invtlb`, `tlb*`, `ertn`, `idle`,
`break`, `syscall`, `dbcl`, plus the transactional-memory `x86settm` family).
Leave `throughput` empty and say so in `notes`. `vldx`-style `.word`-encoded or
undocumented encodings may hang the machine — do not probe unknown encodings
without a timeout.

## Quick checklist for a new instruction

1. Check the vendor list / the intrinsics guide for the exact form and operands.
2. Preserve the existing JSON structure; only touch `latencies`, `throughput`,
   `notes`, and the experiment fields.
3. Latency: for a variable-latency instruction pick a fixed-point, oscillation
   or calibrated reset chain (see `docs/methodology.md`) and split the result
   into `cases` (see `docs/analysis.md` for the models). Otherwise one chain per
   source operand, `--method slope --unroll 1000
   --unroll2 4000`, three runs, and a second closure to confirm.
4. Throughput: 8 disjoint lanes, `--method slope --unroll 250 --unroll2 1000`,
   five runs; check the 0x01 counter if the instruction may be multi-uop.
5. Round to whole cycles, keep the raw measurement in `notes`.
6. `uv run python tools/validate_data.py` (no pipes), then build the site.
7. Do not commit or push unless asked.
