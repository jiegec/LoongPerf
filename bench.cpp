// bench - a small nanoBench-like microbenchmark tool for LoongArch.
//
// It assembles the instructions given with --asm, unrolls them, runs the
// resulting code while counting hardware instructions and core cycles with
// perf_event_open(2), and prints the counts normalised to one execution of the
// original assembly sequence.
//
// Example:
//   ./bench --unroll 100 --warmup 10 --asm "vfmadd.s $vr0, $vr0, $vr2, $vr3"

#include <dlfcn.h>
#include <linux/perf_event.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

struct Options {
  uint64_t unroll = 100;
  uint64_t warmup = 10;
  uint64_t iterations = 10000;
  uint64_t reps = 5;
  int core = 0;
  bool verbose = false;
  std::string asm_text;
  std::string init_text;
  std::vector<uint64_t> counters;
  // slope: measure two body sizes and use the difference; no baseline at all
  // raw: report a single body measurement unchanged
  std::string method = "slope";
  // Second unroll factor for the slope method (0 means 2 * unroll).
  uint64_t unroll2 = 0;
};

struct Counts {
  uint64_t cycles = 0;
  uint64_t instructions = 0;
  std::vector<uint64_t> counters;
};

[[noreturn]] void die(const std::string &msg) {
  std::cerr << "bench: " << msg << "\n";
  std::exit(EXIT_FAILURE);
}

uint64_t parse_u64(const char *what, const char *s) {
  char *end = nullptr;
  errno = 0;
  unsigned long long v = std::strtoull(s, &end, 0);
  if (errno != 0 || end == s || *end != '\0') {
    die(std::string("invalid value for ") + what + ": '" + s + "'");
  }
  return static_cast<uint64_t>(v);
}

void usage(const char *argv0) {
  std::cerr
      << "Usage: " << argv0 << " --asm \"INSTRUCTIONS\" [options]\n"
      << "\n"
      << "Options:\n"
      << "  --asm STRING       LoongArch assembly to measure. Separate\n"
      << "                     instructions with ';' or newlines.\n"
      << "  --init STRING      Assembly to run once before each measurement\n"
      << "                     (e.g. register/flag setup).\n"
      << "  --unroll N         Unroll factor (default: 100)\n"
      << "  --warmup N         Number of warm-up function calls (default: 10)\n"
      << "  --iters N          Function calls per measurement (default: 10000)\n"
      << "  --reps N           Take the minimum over N measurements (default: 5)\n"
      << "  --core N           Pin to CPU N; -1 disables pinning (default: 0)\n"
      << "  --method NAME      slope (default) or raw\n"
      << "                     slope measures two body sizes and differences them,\n"
      << "                     which cancels all per-call and per-measurement cost\n"
      << "                     raw reports one body measurement unchanged\n"
      << "  --unroll2 N        Second unroll factor for --method slope\n"
      << "                     (default: 2 * --unroll)\n"
      << "  --counter CONFIG   Extra raw PMU counter config (repeatable, max 2)\n"
      << "                     e.g. 0xfb = LSX uops, 0xfa = LASX uops\n"
      << "  --verbose          Print measurement details\n"
      << "\n"
      << "Example:\n"
      << "  " << argv0 << " --unroll 1000 --unroll2 4000 --asm "
         "\"fadd.d $f0, $f0, $f1\"\n";
}

Options parse_args(int argc, char **argv) {
  Options opt;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    auto need_value = [&](const char *name) -> const char * {
      if (i + 1 >= argc)
        die(std::string("missing value after ") + name);
      return argv[++i];
    };

    if (arg == "--asm") {
      opt.asm_text = need_value("--asm");
    } else if (arg == "--init") {
      opt.init_text = need_value("--init");
    } else if (arg == "--unroll") {
      opt.unroll = parse_u64("--unroll", need_value("--unroll"));
    } else if (arg == "--warmup") {
      opt.warmup = parse_u64("--warmup", need_value("--warmup"));
    } else if (arg == "--iters") {
      opt.iterations = parse_u64("--iters", need_value("--iters"));
    } else if (arg == "--reps") {
      opt.reps = parse_u64("--reps", need_value("--reps"));
    } else if (arg == "--core") {
      opt.core = static_cast<int>(parse_u64("--core", need_value("--core")));
    } else if (arg == "--unroll2") {
      opt.unroll2 = parse_u64("--unroll2", need_value("--unroll2"));
    } else if (arg == "--method") {
      opt.method = need_value("--method");
    } else if (arg == "--counter") {
      opt.counters.push_back(parse_u64("--counter", need_value("--counter")));
    } else if (arg == "--verbose") {
      opt.verbose = true;
    } else if (arg == "--help") {
      usage(argv[0]);
      std::exit(EXIT_SUCCESS);
    } else {
      die("unknown option '" + arg + "' (use --help for help)");
    }
  }

  if (opt.asm_text.empty())
    die("no assembly given (use --asm \"...\")");
  if (opt.unroll == 0)
    die("--unroll must be greater than zero");
  if (opt.iterations == 0)
    die("--iters must be greater than zero");
  if (opt.reps == 0)
    die("--reps must be greater than zero");
  if (opt.method != "slope" && opt.method != "raw")
    die("--method must be either slope or raw");
  if (opt.method == "raw" && opt.unroll2 != 0)
    die("--unroll2 is only meaningful with --method slope");
  if (opt.method == "slope" && opt.unroll2 != 0 && opt.unroll2 <= opt.unroll)
    die("--unroll2 must be greater than --unroll");
  if (opt.counters.size() > 2)
    die("--counter may be given at most twice (4 PMU counters total)");
  return opt;
}

// Turn the --asm argument into one instruction/directive per line.  Semicolons
// are the conventional separator in nanoBench-like command lines.
std::vector<std::string> split_asm(std::string text) {
  // Allow '\n' and '\t' to be passed literally through a double-quoted shell
  // argument, e.g. --asm 'add.d ...\nadd.d ...'.
  std::string unescaped;
  unescaped.reserve(text.size());
  for (size_t i = 0; i < text.size(); ++i) {
    if (text[i] == '\\' && i + 1 < text.size()) {
      if (text[i + 1] == 'n') {
        unescaped.push_back('\n');
        ++i;
        continue;
      }
      if (text[i + 1] == 't') {
        unescaped.push_back('\t');
        ++i;
        continue;
      }
    }
    unescaped.push_back(text[i]);
  }

  std::vector<std::string> lines;
  std::string line;
  std::istringstream input(unescaped);
  while (std::getline(input, line, ';')) {
    // A newline inside a getline result is still possible.
    std::istringstream sub(line);
    std::string part;
    while (std::getline(sub, part)) {
      auto first = part.find_first_not_of(" \t\r");
      auto last = part.find_last_not_of(" \t\r");
      if (first == std::string::npos)
        continue;
      lines.push_back(part.substr(first, last - first + 1));
    }
  }
  if (lines.empty())
    die("the assembly contains no instructions");
  return lines;
}

std::string make_temp_dir() {
  char tmpl[] = "/tmp/loongperf-bench-XXXXXX";
  char *dir = mkdtemp(tmpl);
  if (!dir)
    die(std::string("mkdtemp failed: ") + std::strerror(errno));
  return dir;
}

std::string read_file(const std::string &path) {
  std::ifstream in(path);
  std::ostringstream out;
  out << in.rdbuf();
  return out.str();
}

void write_file(const std::string &path, const std::string &contents) {
  std::ofstream out(path);
  if (!out)
    die("cannot write " + path);
  out << contents;
  if (!out)
    die("failed while writing " + path);
}

std::string generate_assembly(const std::vector<std::string> &insns,
                              const std::vector<std::string> &init_insns,
                              uint64_t unroll) {
  const char *save_regs[] = {"$r1",  "$r22", "$r23", "$r24", "$r25", "$r26",
                             "$r27", "$r28", "$r29", "$r30", "$r31"};
  const int nregs = static_cast<int>(sizeof(save_regs) / sizeof(save_regs[0]));
  const int frame_size = 96;  // 11 * 8 rounded up to a 16-byte boundary

  std::ostringstream out;
  out << "    .text\n";

  auto emit_func = [&](const char *name, bool with_body) {
    out << "    .globl " << name << "\n";
    out << "    .type " << name << ",@function\n";
    out << "    .p2align 6\n";
    out << name << ":\n";
    out << "    addi.d $sp, $sp, -" << frame_size << "\n";
    for (int i = 0; i < nregs; ++i)
      out << "    st.d " << save_regs[i] << ", $sp, " << i * 8 << "\n";
    for (const auto &insn : init_insns)
      out << "    " << insn << "\n";
    if (with_body) {
      out << "    .rept " << unroll << "\n";
      for (const auto &insn : insns)
        out << "    " << insn << "\n";
      out << "    .endr\n";
    }
    for (int i = 0; i < nregs; ++i)
      out << "    ld.d " << save_regs[i] << ", $sp, " << i * 8 << "\n";
    out << "    addi.d $sp, $sp, " << frame_size << "\n";
    out << "    jr $r1\n";
    out << "    .size " << name << ", .-" << name << "\n";
  };

  emit_func("bench_body", true);
  out << "    .section .note.GNU-stack,\"\",@progbits\n";
  return out.str();
}

void bind_to_cpu(int cpu) {
  if (cpu < 0)
    return;
  cpu_set_t set;
  CPU_ZERO(&set);
  CPU_SET(cpu, &set);
  if (sched_setaffinity(0, sizeof(set), &set) != 0) {
    std::cerr << "bench: warning: could not pin to CPU " << cpu << ": "
              << std::strerror(errno) << "\n";
  }
}

int perf_event_open_wrapper(struct perf_event_attr *attr, pid_t pid, int cpu,
                            int group_fd, unsigned long flags) {
  return static_cast<int>(syscall(SYS_perf_event_open, attr, pid, cpu, group_fd,
                                  flags));
}

int open_perf_counter(uint64_t config, int group_fd,
                      uint32_t type = PERF_TYPE_HARDWARE) {
  struct perf_event_attr attr;
  std::memset(&attr, 0, sizeof(attr));
  attr.type = type;
  attr.size = sizeof(attr);
  attr.config = config;
  attr.disabled = (group_fd == -1) ? 1 : 0;
  // The perf core rejects a pinned group member; pin only the leader.
  attr.pinned = (group_fd == -1) ? 1 : 0;
  attr.exclude_kernel = 1;
  attr.exclude_hv = 1;
  attr.inherit = 0;

  int fd = perf_event_open_wrapper(&attr, 0, -1, group_fd, 0);
  if (fd < 0) {
    die(std::string("perf_event_open failed: ") + std::strerror(errno) +
        "\n(hint: run as root, or lower kernel.perf_event_paranoid)");
  }
  return fd;
}

struct PerfCounters {
  int insn_fd = -1;
  int cycle_fd = -1;
  std::vector<int> counter_fds;

  explicit PerfCounters(const std::vector<uint64_t> &counters) {
    // Make instructions the group leader so that all counters are enabled
    // and disabled for exactly the same interval.
    insn_fd = open_perf_counter(PERF_COUNT_HW_INSTRUCTIONS, -1);
    cycle_fd = open_perf_counter(PERF_COUNT_HW_CPU_CYCLES, insn_fd);
    for (uint64_t config : counters)
      counter_fds.push_back(
          open_perf_counter(config, insn_fd, PERF_TYPE_RAW));
  }

  ~PerfCounters() {
    for (int fd : counter_fds)
      if (fd >= 0)
        close(fd);
    if (cycle_fd >= 0)
      close(cycle_fd);
    if (insn_fd >= 0)
      close(insn_fd);
  }

  void start() {
    if (ioctl(insn_fd, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP) != 0)
      die(std::string("PERF_EVENT_IOC_RESET failed: ") + std::strerror(errno));
    if (ioctl(insn_fd, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP) != 0)
      die(std::string("PERF_EVENT_IOC_ENABLE failed: ") + std::strerror(errno));
  }

  Counts stop() {
    if (ioctl(insn_fd, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP) != 0)
      die(std::string("PERF_EVENT_IOC_DISABLE failed: ") +
          std::strerror(errno));

    Counts c;
    if (read(insn_fd, &c.instructions, sizeof(c.instructions)) !=
        sizeof(c.instructions))
      die(std::string("reading instruction counter failed: ") +
          std::strerror(errno));
    if (read(cycle_fd, &c.cycles, sizeof(c.cycles)) != sizeof(c.cycles))
      die(std::string("reading cycle counter failed: ") +
          std::strerror(errno));
    c.counters.resize(counter_fds.size());
    for (size_t i = 0; i < counter_fds.size(); ++i) {
      if (read(counter_fds[i], &c.counters[i], sizeof(c.counters[i])) !=
          sizeof(c.counters[i]))
        die(std::string("reading custom counter failed: ") +
            std::strerror(errno));
    }
    return c;
  }
};

using BenchFn = void (*)();

Counts measure(BenchFn fn, uint64_t iterations, PerfCounters &perf) {
  perf.start();
  for (uint64_t i = 0; i < iterations; ++i)
    fn();
  return perf.stop();
}

template <typename T> T minimum(T a, T b) { return a < b ? a : b; }

}  // namespace

int main(int argc, char **argv) {
  Options opt = parse_args(argc, argv);
  std::vector<std::string> insns = split_asm(opt.asm_text);
  std::vector<std::string> init_insns;
  if (!opt.init_text.empty())
    init_insns = split_asm(opt.init_text);

  bind_to_cpu(opt.core);

  const std::string dir = make_temp_dir();

  using BenchFn = void (*)();
  struct Loaded {
    void *handle = nullptr;
    BenchFn body = nullptr;
  };

  auto build = [&](const char *name, uint64_t unroll) -> Loaded {
    const std::string src = dir + "/" + name + ".S";
    const std::string so = dir + "/" + name + ".so";
    const std::string log = dir + "/" + name + ".log";
    write_file(src, generate_assembly(insns, init_insns, unroll));
    std::string cmd =
        "gcc -shared -nostdlib -fPIC -o " + so + " " + src + " >" + log + " 2>&1";
    if (std::system(cmd.c_str()) != 0) {
      std::cerr << "bench: failed to assemble the input instructions\n\n"
                << read_file(log);
      std::exit(EXIT_FAILURE);
    }
    void *handle = dlopen(so.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!handle)
      die(std::string("dlopen failed: ") + dlerror());
    Loaded l;
    l.handle = handle;
    l.body = reinterpret_cast<BenchFn>(dlsym(handle, "bench_body"));
    if (!l.body)
      die(std::string("dlsym failed: ") + dlerror());
    return l;
  };

  const bool use_slope = (opt.method == "slope");
  const uint64_t unroll_a = opt.unroll;
  const uint64_t unroll_b =
      use_slope ? (opt.unroll2 != 0 ? opt.unroll2 : opt.unroll * 2) : 0;

  Loaded a = build("bench_a", unroll_a);
  Loaded b;
  if (use_slope)
    b = build("bench_b", unroll_b);

  // Touch the code and warm up caches/branch predictors before measuring.
  for (uint64_t i = 0; i < opt.warmup; ++i) {
    a.body();
    if (use_slope)
      b.body();
  }

  PerfCounters perf(opt.counters);
  const size_t ncounters = opt.counters.size();

  // Number of sequences spanned by one difference (slope) or one measurement
  // (raw). All reported values are normalised by this span.
  const double span = static_cast<double>(opt.iterations) *
                      static_cast<double>(use_slope ? (unroll_b - unroll_a)
                                                    : unroll_a);

  uint64_t best_cycles = UINT64_MAX;
  uint64_t best_insns = 0;
  std::vector<uint64_t> best_counters(ncounters, 0);
  uint64_t diag_cycles_a = 0, diag_cycles_b = 0;

  for (uint64_t rep = 0; rep < opt.reps; ++rep) {
    Counts ma = measure(a.body, opt.iterations, perf);
    Counts mb;
    if (use_slope)
      mb = measure(b.body, opt.iterations, perf);

    uint64_t dcycles, dinsns;
    std::vector<uint64_t> dcounters(ncounters, 0);
    if (use_slope) {
      if (mb.cycles < ma.cycles)
        die("slope counter underflow; perf counters look unreliable");
      if (mb.instructions < ma.instructions)
        die("slope instruction counter underflow; perf counters look "
            "unreliable");
      dcycles = mb.cycles - ma.cycles;
      dinsns = mb.instructions - ma.instructions;
      for (size_t i = 0; i < ncounters; ++i)
        dcounters[i] = mb.counters[i] >= ma.counters[i]
                           ? mb.counters[i] - ma.counters[i]
                           : 0;
    } else {
      dcycles = ma.cycles;
      dinsns = ma.instructions;
      dcounters = ma.counters;
    }

    if (dcycles < best_cycles) {
      best_cycles = dcycles;
      best_insns = dinsns;
      best_counters = dcounters;
      diag_cycles_a = ma.cycles;
      diag_cycles_b = use_slope ? mb.cycles : 0;
    }
  }

  if (opt.verbose) {
    std::cout << "asm: " << opt.asm_text << "\n";
    if (!opt.init_text.empty())
      std::cout << "init: " << opt.init_text << "\n";
    std::cout << "method: " << opt.method << "\n";
    std::cout << "instructions per unrolled copy: " << insns.size() << "\n";
    std::cout << "unroll: " << unroll_a;
    if (use_slope)
      std::cout << " and " << unroll_b;
    std::cout << "\n";
    std::cout << "warmup: " << opt.warmup << "\n";
    std::cout << "iterations: " << opt.iterations << "\n";
    std::cout << "reps: " << opt.reps << "\n";
    std::cout << "raw counter cycles: " << diag_cycles_a;
    if (use_slope)
      std::cout << " (unroll " << unroll_a << ") and " << diag_cycles_b
                << " (unroll " << unroll_b << ")";
    std::cout << "\n";
  }

  std::printf("instructions retired: %.2f",
              static_cast<double>(best_insns) / span);
  std::printf("\ncycles: %.2f", static_cast<double>(best_cycles) / span);
  std::printf("\n");
  for (size_t i = 0; i < ncounters; ++i)
    std::printf("counter 0x%llx: %.2f\n",
                static_cast<unsigned long long>(opt.counters[i]),
                static_cast<double>(best_counters[i]) / span);

  dlclose(a.handle);
  if (use_slope)
    dlclose(b.handle);
  std::string cleanup = "rm -rf " + dir;
  if (std::system(cleanup.c_str()) != 0)
    std::cerr << "bench: warning: could not remove " << dir << "\n";
  return 0;
}
