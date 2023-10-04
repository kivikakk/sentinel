from amaranth import Cat, C, Module, Signal, Elaboratable, Memory
from amaranth.lib.wiring import Component, Signature, In, Out

from sentinel.ucoderom import UCodeROM, UCodeFieldClasses


# Support loading microcode enums from file.
def load_ucode_fields(ucode: UCodeFieldClasses | str):
    if not isinstance(ucode, UCodeFieldClasses):
        # Enums from microcode ROM.
        return UCodeROM(main_file=ucode).field_classes
    else:
        # Or use the enums as-is.
        return ucode


def pc_signature(ucode: UCodeFieldClasses):
    return Signature({
        "pc": In(32),
        "action": Out(ucode.PcAction),
        "dat_w": Out(30)
    })


class ProgramCounter(Elaboratable):
    @property
    def signature(self):
        return pc_signature(self.ucode)

    def __init__(self, ucode: UCodeFieldClasses = ""):
        self.ucode = load_ucode_fields(ucode)
        self.pc = Signal(32)
        self.action = Signal(self.ucode.PcAction)
        self.dat_w = Signal(30)

    def elaborate(self, platform):
        m = Module()

        with m.Switch(self.action):
            with m.Case(self.ucode.PcAction.INC):
                m.d.sync += self.pc.eq(self.pc + 4)
            with m.Case(self.ucode.PcAction.LOAD):
                m.d.sync += self.pc.eq(Cat(C(0, 2), self.dat_w))

        return m


class RegFile(Elaboratable):
    def __init__(self, ucode: UCodeFieldClasses = ""):
        self.ucode = load_ucode_fields(ucode)
        self.adr = Signal(5)
        self.dat_r = Signal(32)
        self.dat_w = Signal(32)
        self.action = Signal(self.ucode.RegOp)
        self.mem = Memory(width=32, depth=32)

    def elaborate(self, platform):
        m = Module()

        adr_prev = Signal.like(self.adr)

        # Re: transparent, let's attempt to save some resources for now.
        m.submodules.rdport = rdport = self.mem.read_port()
        m.submodules.wrport = wrport = self.mem.write_port()

        m.d.comb += [
            rdport.addr.eq(self.adr),
            wrport.addr.eq(self.adr),
            wrport.data.eq(self.dat_w),
        ]

        # We have to simulate a single cycle latency for accessing the zero
        # reg.
        m.d.sync += adr_prev.eq(self.adr)

        # Zero register logic- ignore writes/return 0 for reads.
        with m.If((adr_prev == 0) & (self.adr == 0)):
            m.d.comb += self.dat_r.eq(0)
        with m.Else():
            m.d.comb += [
                self.dat_r.eq(rdport.data),
                wrport.en.eq(self.action == self.ucode.RegOp.WRITE_DST)
            ]

        return m


def data_path_ctrl_signature(ucode: UCodeFieldClasses):
    return Signature({
        "gp_action": Out(ucode.RegOp),
        "pc_action": Out(ucode.PcAction)
    })


class DataPath(Component):
    @property
    def signature(self):
        return Signature({
            "gp": Out(Signature({
                "adr": Out(5),
                "dat_r": In(32),
                "dat_w": Out(32),
            })),
            "pc": Out(Signature({
                "dat_r": In(32),
                "dat_w": Out(32),
            })),
            "ctrl": Out(data_path_ctrl_signature(self.ucode))
        }).flip()

    def __init__(self, ucode: UCodeFieldClasses = ""):
        self.ucode = load_ucode_fields(ucode)
        super().__init__()

        self.pc_mod = ProgramCounter(self.ucode)
        self.regfile = RegFile(self.ucode)

    def elaborate(self, platform):
        m = Module()

        m.submodules.pc_mod = self.pc_mod
        m.submodules.regfile = self.regfile

        m.d.comb += [
            self.regfile.adr.eq(self.gp.adr),
            self.regfile.dat_w.eq(self.gp.dat_w),
            self.regfile.action.eq(self.ctrl.gp_action),
            self.gp.dat_r.eq(self.regfile.dat_r),

            self.pc_mod.action.eq(self.ctrl.pc_action),
            self.pc.dat_r.eq(self.pc_mod.pc),
            self.pc_mod.dat_w.eq(self.pc.dat_w[2:])
        ]

        return m
