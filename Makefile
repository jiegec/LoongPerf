CXX ?= g++
CXXFLAGS ?= -O2 -std=gnu++17 -Wall -Wextra

.PHONY: all clean

all: bench

bench: bench.cpp
	$(CXX) $(CXXFLAGS) -o $@ $< -ldl

clean:
	rm -f bench
