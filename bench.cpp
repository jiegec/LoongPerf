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
};

struct Counts {
  uint64_t cycles = 0;
  uint64_t instructions = 0;
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
      << "  --unroll N         Unroll factor (default: 100)\n"
      << "  --warmup N         Number of warm-up function calls (default: 10)\n"
      << "  --iters N          Function calls per measurement (default: 10000)\n"
      << "  --reps N           Take the minimum over N measurements (default: 5)\n"
      << "  --core N           Pin to CPU N; -1 disables pinning (default: 0)\n"
      << "  --verbose          Print measurement details\n"
      << "\n"
      << "Example:\n"
      << "  " << argv0 << " --unroll 100 --warmup 10 "
         "--asm \"vfmadd.s $vr0, $vr0, $vr2, $vr3\"\n";
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
  emit_func("bench_empty", false);
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

int open_perf_counter(uint64_t config, int group_fd) {
  struct perf_event_attr attr;
  std::memset(&attr, 0, sizeof(attr));
  attr.type = PERF_TYPE_HARDWARE;
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

  PerfCounters() {
    // Make instructions the group leader so that both counters are enabled
    // and disabled for exactly the same interval.
    insn_fd = open_perf_counter(PERF_COUNT_HW_INSTRUCTIONS, -1);
    cycle_fd = open_perf_counter(PERF_COUNT_HW_CPU_CYCLES, insn_fd);
  }

  ~PerfCounters() {
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

  bind_to_cpu(opt.core);

  const std::string dir = make_temp_dir();
  const std::string src_path = dir + "/bench.S";
  const std::string so_path = dir + "/bench.so";
  const std::string log_path = dir + "/build.log";

  write_file(src_path, generate_assembly(insns, opt.unroll));

  std::string cmd = "gcc -shared -nostdlib -fPIC -o " + so_path + " " +
                    src_path + " >" + log_path + " 2>&1";
  int rc = std::system(cmd.c_str());
  if (rc != 0) {
    std::cerr << "bench: failed to assemble the input instructions\n\n"
              << read_file(log_path);
    std::exit(EXIT_FAILURE);
  }

  void *handle = dlopen(so_path.c_str(), RTLD_NOW | RTLD_LOCAL);
  if (!handle)
    die(std::string("dlopen failed: ") + dlerror());

  auto body = reinterpret_cast<BenchFn>(dlsym(handle, "bench_body"));
  auto empty = reinterpret_cast<BenchFn>(dlsym(handle, "bench_empty"));
  if (!body || !empty)
    die(std::string("dlsym failed: ") + dlerror());

  // Touch the code and warm up caches/branch predictors before measuring.
  for (uint64_t i = 0; i < opt.warmup; ++i) {
    empty();
    body();
  }

  PerfCounters perf;
  uint64_t best_cycles = UINT64_MAX;
  uint64_t best_insns = 0;
  uint64_t best_overhead_cycles = 0;
  uint64_t best_overhead_insns = 0;

  for (uint64_t rep = 0; rep < opt.reps; ++rep) {
    Counts overhead = measure(empty, opt.iterations, perf);
    Counts measured = measure(body, opt.iterations, perf);

    if (measured.cycles < overhead.cycles)
      die("cycle counter underflow; perf counters look unreliable");
    if (measured.instructions < overhead.instructions)
      die("instruction counter underflow; perf counters look unreliable");

    uint64_t body_cycles = measured.cycles - overhead.cycles;
    uint64_t body_insns = measured.instructions - overhead.instructions;

    if (body_cycles < best_cycles) {
      best_cycles = body_cycles;
      best_insns = body_insns;
      best_overhead_cycles = overhead.cycles;
      best_overhead_insns = overhead.instructions;
    }
  }

  const double divisor =
      static_cast<double>(opt.iterations) * static_cast<double>(opt.unroll);
  const double cycles_per_sequence =
      static_cast<double>(best_cycles) / divisor;
  const double insns_per_sequence =
      static_cast<double>(best_insns) / divisor;

  if (opt.verbose) {
    std::cout << "asm: " << opt.asm_text << "\n";
    std::cout << "instructions per unrolled copy: " << insns.size() << "\n";
    std::cout << "unroll: " << opt.unroll << "\n";
    std::cout << "warmup: " << opt.warmup << "\n";
    std::cout << "iterations: " << opt.iterations << "\n";
    std::cout << "reps: " << opt.reps << "\n";
    std::cout << "empty baseline: " << best_overhead_insns
              << " instructions, " << best_overhead_cycles << " cycles\n";
  }

  std::printf("instructions retired: %.2f\n", insns_per_sequence);
  std::printf("cycles: %.2f\n", cycles_per_sequence);

  dlclose(handle);
  std::string cleanup = "rm -rf " + dir;
  if (std::system(cleanup.c_str()) != 0) {
    std::cerr << "bench: warning: could not remove " << dir << "\n";
  }
  return 0;
}
