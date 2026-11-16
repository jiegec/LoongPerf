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
