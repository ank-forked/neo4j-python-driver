#!/usr/bin/env python
#! -*- encoding: UTF-8 -*-

# Copyright (c) 2002-2015 "Neo Technology,"
# Network Engine for Objects in Lund AB [http://neotechnology.com]
#
# This file is part of Neo4j.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
python -m neo4j.bench
"""

from __future__ import print_function, division

from itertools import chain
from multiprocessing import Array, Process
from os import getenv
from os.path import basename
import sys
import subprocess
from math import log, ceil, floor
try:
    from time import perf_counter
except ImportError:
    from time import clock as perf_counter

from neo4j import GraphDatabase


USAGE = """\
Usage: {script} [«options»] «statement» [ «statement» ... ]

Test performance of one or more Cypher statements against a
Neo4j server.

Options:
  -? --help              display this help text
  -x --times COUNT       number of times to execute each statement
                         (default 2500)
  -p --parallels VALUES  comma separated list of parallel values
                         (default: 1,2,4,8,16)

Environment:
  NEO4J_URI - base URI of Neo4j database, e.g. neo4j://localhost

Report bugs to nigel@neotechnology.com
"""


def percentile(values, nth):
    index = (len(values) - 1) * nth
    lo = floor(index)
    hi = ceil(index)
    if lo == hi:
        return values[int(index)]
    else:
        return values[lo] * (hi - index) + values[hi] * (index - lo)


def microseconds(t):
    return "{:,.1f}\xB5s".format(t * 1000000)


def print_bench(overall_latencies, network_latencies, wait_latencies):
    print("------------------------------------------------------")
    print(" percentile |   overall   |   network   |     wait    ")
    print("------------|-------------|-------------|-------------")
    for p in [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 98, 99, 99.5, 99.9, 100]:
        overall_latency = percentile(overall_latencies, p / 100)
        network_latency = percentile(network_latencies, p / 100)
        wait_latency = percentile(wait_latencies, p / 100)
        if p in (50, 90, 99, 99.9):
            kwargs = {
                "perc": yellow("{:5.1f}%".format(p)),
                "over": yellow(microseconds(overall_latency).rjust(11)),
                "netw": yellow(microseconds(network_latency).rjust(11)),
                "wait": yellow(microseconds(wait_latency).rjust(11)),
            }
        else:
            kwargs = {
                "perc": "{:5.1f}%".format(p),
                "over": microseconds(overall_latency).rjust(11),
                "netw": microseconds(network_latency).rjust(11),
                "wait": microseconds(wait_latency).rjust(11),
            }
        print("     {perc} | {over} | {netw} | {wait}".format(**kwargs))


def yellow(s):
    return "\x1b[32;1m%s\x1b[0m" % s    # yellow


def processors():
    p = 0
    output = subprocess.check_output("cat /proc/cpuinfo", shell=True).strip()
    for line in output.split(b"\n"):
        if line.startswith(b"processor"):
            p += 1
    return p


class GAP(Process):

    @classmethod
    def run_all(cls, process_count, run_count, statement):
        print("------------------------------------------------------")
        print(" %s" % statement)

        overall = [Array("d", run_count) for _ in range(process_count)]
        network = [Array("d", run_count) for _ in range(process_count)]
        wait = [Array("d", run_count) for _ in range(process_count)]
        processes = [cls(run_count, statement, overall[i], network[i], wait[i])
                     for i in range(process_count)]

        t0 = perf_counter()
        for process in processes:
            process.start()
        for process in processes:
            process.join()
        t = perf_counter() - t0

        process_run_count = process_count * run_count
        tx_per_second = process_run_count / t
        print("   × {:,} runs × {} client{}".format(run_count, process_count,
                                                    "" if process_count == 1 else "s"), end="")
        print(" = " + yellow("{:,.1f} tx/s".format(tx_per_second)))
        print("    ({:,} requests in {:.1f}s)".format(process_run_count, t))
        print_bench(sorted(chain(*overall)), sorted(chain(*network)), sorted(chain(*wait)))
        print()

    def __init__(self, run_count, statement, overall, network, wait):
        super(Process, self).__init__()
        self.run_count = run_count
        self.statement = statement
        self.overall = overall
        self.network = network
        self.wait = wait
        self.driver = GraphDatabase.driver(getenv("NEO4J_GAP_URI", "gap://localhost"), bench=True)
        self.session = self.driver.session()

    def run(self):
        session = self.session
        run = session.run
        statement = self.statement
        run_count = self.run_count
        for i in range(run_count):
            run(statement)
        bench = session.bench
        for i, latency in enumerate(bench):
            self.overall[i] = latency.overall
            self.network[i] = latency.network
            self.wait[i] = latency.wait


def help_(**kwargs):
    print(USAGE.format(**kwargs))


def main():
    processor_count = processors()
    print("Neo4j Benchmarking Tool for Python")
    print("Copyright (c) 2002-2015 \"Neo Technology,\"")
    print("Network Engine for Objects in Lund AB [http://neotechnology.com]")
    print("Report bugs to nigel@neotechnology.com")
    print()
    print("This machine has %d processors" % processor_count)
    print()
    print("Latency measurements:")
    print("  overall = time between start and end of method call")
    print("  network = time between start of send and end of receive")
    print("  wait    = time between end of send and start of receive")
    print()
    script, args = sys.argv[0], sys.argv[1:]
    run_count = 10000
    statements = []
    parallels = list(2 ** n for n in range(int(ceil(log(16, 2)) + 1)))
    while args:
        arg = args.pop(0)
        if arg.startswith("-"):
            if arg in ("-h", "--help"):
                help_(script=basename(script))
                sys.exit(0)
            elif arg in ("-x", "--times"):
                run_count = int(args.pop(0))
            elif arg in ("-p", "--parallels"):
                parallels = list(map(int, args.pop(0).split(",")))
            else:
                print("Unknown option %r" % arg)
                sys.exit(1)
        else:
            statements.append(arg)
    if not statements:
        print("No statements specified, using defaults")
        session = GraphDatabase.driver(getenv("NEO4J_GAP_URI", "gap://localhost")).session()
        session.run("CREATE CONSTRAINT ON (a:Thing) ASSERT a.foo IS UNIQUE")
        results = session.run("MERGE (a:Thing {foo:'bar'}) RETURN id(a)")
        node_id = results[0][0]
        session.close()
        statements = ["unwind(range(1, %d)) AS z RETURN z" % n for n in [0, 1, 10, 100]] + \
                     ["MATCH (a) WHERE id(a) = %d RETURN a" % node_id,
                      "MATCH (a:Thing) WHERE a.foo = 'bar' RETURN a"]
    for statement in statements:
        for process_count in parallels:
            GAP.run_all(process_count, run_count, statement)

        # print()


if __name__ == "__main__":
    main()