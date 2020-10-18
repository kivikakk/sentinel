import sys
import argparse
import subprocess
import re

from nmigen import *
from nmigen.back import rtlil, cxxrtl, verilog
from nmigen.sim import *

from .alu import ALU
from .control import Control
from .datapath import DataPath
from .decode import Decode
from .top import Top

class RunnerError(Exception):
    pass


def main_parser(parser=None):
    # nmigen.cli begin
    if parser is None:
        parser = argparse.ArgumentParser()

    p_action = parser.add_subparsers(dest="action")

    p_generate = p_action.add_parser("generate",
        help="generate RTLIL, Verilog or CXXRTL from the design")
    p_generate.add_argument("-t", "--type", dest="generate_type",
        metavar="LANGUAGE", choices=["il", "cc", "v"],
        help="generate LANGUAGE (il for RTLIL, v for Verilog, cc for CXXRTL; default: file extension of FILE, if given)")
    p_generate.add_argument("generate_file",
        metavar="FILE", type=argparse.FileType("w"), nargs="?",
        help="write generated code to FILE")

    p_simulate = p_action.add_parser(
        "simulate", help="simulate the design")
    p_simulate.add_argument("-v", "--vcd-file",
        metavar="VCD-FILE", type=argparse.FileType("w"),
        help="write execution trace to VCD-FILE")
    p_simulate.add_argument("-w", "--gtkw-file",
        metavar="GTKW-FILE", type=argparse.FileType("w"),
        help="write GTKWave configuration to GTKW-FILE")
    p_simulate.add_argument("-p", "--period", dest="sync_period",
        metavar="TIME", type=float, default=1e-6,
        help="set 'sync' clock domain period to TIME (default: %(default)s)")
    p_simulate.add_argument("-c", "--clocks", dest="sync_clocks",
        metavar="COUNT", type=int, required=True,
        help="simulate for COUNT 'sync' clock periods")
    # nmigen.cli end

    p_size = p_action.add_parser("size",
        help="Run a generic synth script to query design size.")
    p_size.add_argument("-v", "--verbose", action="store_true",
        help="Show full yosys output, not just stats.")

    return parser


def main_runner(parser, args, design, platform=None, name="top", ports=()):
    # nmigen.cli begin
    if args.action == "generate":
        fragment = Fragment.get(design, platform)
        generate_type = args.generate_type
        if generate_type is None and args.generate_file:
            if args.generate_file.name.endswith(".il"):
                generate_type = "il"
            if args.generate_file.name.endswith(".cc"):
                generate_type = "cc"
            if args.generate_file.name.endswith(".v"):
                generate_type = "v"
        if generate_type is None:
            parser.error("Unable to auto-detect language, specify explicitly with -t/--type")
        if generate_type == "il":
            output = rtlil.convert(fragment, name=name, ports=ports)
        if generate_type == "cc":
            output = cxxrtl.convert(fragment, name=name, ports=ports)
        if generate_type == "v":
            output = verilog.convert(fragment, name=name, ports=ports)
        if args.generate_file:
            args.generate_file.write(output)
        else:
            print(output)

    if args.action == "simulate":
        fragment = Fragment.get(design, platform)
        sim = Simulator(fragment)
        sim.add_clock(args.sync_period)
        design.sim_hooks(sim)
        with sim.write_vcd(vcd_file=args.vcd_file, gtkw_file=args.gtkw_file, traces=ports):
            sim.run_until(args.sync_period * args.sync_clocks, run_passive=True)
    # nmigen.cli end

    if args.action == "size":
        fragment = Fragment.get(design, platform)
        rtlil_text = rtlil.convert(fragment, name=name, ports=ports)

        # Created from a combination of nmigen._toolchain.yosys and
        # nmigen.back.verilog. Script comes from nextpnr-generic.
        script = []
        script.append("read_ilang <<rtlil\n{}\nrtlil".format(rtlil_text))
        script.append("hierarchy -check")
        script.append("proc")
        script.append("flatten")
        script.append("tribuf -logic")
        script.append("deminout")
        script.append("synth -run coarse")
        script.append("memory_map")
        script.append("opt -full")
        script.append("techmap -map +/techmap.v")
        script.append("opt -fast")
        script.append("dfflegalize -cell $_DFF_P_ 0")
        script.append("abc -lut 4 -dress")
        script.append("clean")
        script.append("hierarchy -check")
        script.append("stat")

        stdin = "\n".join(script)

        popen = subprocess.Popen(["yosys", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8")
        stdout, stderr = popen.communicate(stdin)
        if popen.returncode:
            raise RunnerError(stderr.strip())

        if args.verbose:
            print(stdout)
        else:
            begin_re = re.compile(r"[\d.]+ Printing statistics.")
            end_re = re.compile(r"End of script.")
            capture = False
            # begin_l = 0
            # end_l = 0

            for i, l in enumerate(stdout.split("\n")):
                if begin_re.match(l):
                    capture = True

                if end_re.match(l):
                    capture = False

                if capture:
                    print(l)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--module", dest="module",
        metavar="MODULE", choices=["ALU", "Control", "DataPath", "Decode", "Top"],
        default = "Top", help="generate code for module.")

    # In nmigen.cli, these are passed straight to main_runner. We need
    # different main_runner depending on component.
    main_p = main_parser(parser)
    args = parser.parse_args()

    if args.module == "ALU":
        mod = ALU(width=32)
    elif args.module == "Control":
        mod = Control()
    elif args.module == "DataPath":
        mod = DataPath()
    elif args.module == "Decode":
        mod = Decode()
    elif args.module == "Top":
        mod = Top()
    else:
        assert False

    main_runner(parser, args, mod, ports=mod.ports())
