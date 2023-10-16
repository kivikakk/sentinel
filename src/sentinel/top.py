from amaranth import Signal, Elaboratable, Module, Cat, C
from amaranth.lib.wiring import Component, Signature, Out, connect
from amaranth_soc import wishbone

from .alu import ALU
from .control import Control
from .datapath import DataPath
from .decode import Decode
from .ucodefields import ASrc, BSrc, SrcOp, RegRSel, MemSel, PcAction


RVFISignature = Signature({
    "valid": Out(1),
    "order": Out(64),
    "insn": Out(32),
    "trap": Out(1),
    "halt": Out(1),
    "intr":  Out(1),
    "mode": Out(2),
    "ixl": Out(2),
    "rs1_addr": Out(5),
    "rs2_addr": Out(5),
    "rs1_rdata": Out(32),
    "rs2_rdata": Out(32),
    "rd_addr": Out(5),
    "rd_wdata": Out(32),
    "pc_rdata": Out(32),
    "pc_wdata": Out(32),
    "mem_addr": Out(32),
    "mem_rmask": Out(4),
    "mem_wmask": Out(4),
    "mem_rdata": Out(32),
    "mem_wdata": Out(32)
})


class Top(Component):
    @property
    def signature(self):
        if self.formal:
            return Signature({
                "bus": Out(wishbone.Signature(addr_width=30, data_width=32,
                                              granularity=8)),
                "rvfi": Out(RVFISignature)
            })
        else:
            return Signature({
                "bus": Out(wishbone.Signature(addr_width=30, data_width=32,
                                              granularity=8)),
            })

    def __init__(self, *, formal=False):
        self.formal = formal
        super().__init__()

        self.req_next = Signal()
        self.insn_fetch_curr = Signal()
        self.insn_fetch_next = Signal()

        ###

        self.alu = ALU(32)
        self.control = Control()
        self.datapath = DataPath(formal=formal)
        self.decode = Decode()

        # ALU
        self.a_input = Signal(32)
        self.b_input = Signal(32)

        # Decode
        self.reg_r_adr = Signal(5)
        self.reg_w_adr = Signal(5)

    def elaborate(self, platform):
        m = Module()

        m.submodules.alu = self.alu
        m.submodules.control = self.control
        m.submodules.datapath = self.datapath
        m.submodules.decode = self.decode

        data_adr = Signal.like(self.alu.data.o)

        # ALU conns
        a_mux_o = Signal(32)
        b_mux_o = Signal(32)
        connect(m, self.alu.ctrl, self.control.alu)

        # ALU conns
        m.d.comb += [
            self.alu.data.a.eq(self.a_input),
            self.alu.data.b.eq(self.b_input),
        ]

        # Connect ALU sources
        with m.If((self.control.src_op == SrcOp.LATCH_A) |
                  (self.control.src_op == SrcOp.LATCH_A_B)):
            with m.Switch(self.control.a_src):
                with m.Case(ASrc.GP):
                    m.d.sync += self.a_input.eq(self.datapath.gp.dat_r)
                with m.Case(ASrc.IMM):
                    m.d.sync += self.a_input.eq(self.decode.imm)
                with m.Case(ASrc.ZERO):
                    m.d.sync += self.a_input.eq(0)
                with m.Case(ASrc.ALU_O):
                    m.d.sync += self.a_input.eq(self.alu.data.o)
                with m.Case(ASrc.FOUR):
                    m.d.sync += self.a_input.eq(4)

        with m.If((self.control.src_op == SrcOp.LATCH_B) |
                  (self.control.src_op == SrcOp.LATCH_A_B)):
            with m.Switch(self.control.b_src):
                with m.Case(BSrc.GP):
                    m.d.sync += self.b_input.eq(self.datapath.gp.dat_r)
                with m.Case(BSrc.IMM):
                    m.d.sync += self.b_input.eq(self.decode.imm)
                with m.Case(BSrc.ONE):
                    m.d.sync += self.b_input.eq(1)
                with m.Case(BSrc.PC):
                    m.d.sync += self.b_input.eq(Cat(C(0, 2),
                                                    self.datapath.pc.dat_r))
                with m.Case(BSrc.DAT_R):
                    with m.Switch(self.control.mem_sel):
                        with m.Case(MemSel.BYTE):
                            with m.If(data_adr[0:2] == 0):
                                m.d.sync += self.b_input.eq(self.bus.dat_r[0:8])  # noqa: E501
                            with m.Elif(data_adr[0:2] == 1):
                                m.d.sync += self.b_input.eq(self.bus.dat_r[8:16])  # noqa: E501
                            with m.Elif(data_adr[0:2] == 2):
                                m.d.sync += self.b_input.eq(self.bus.dat_r[16:24])  # noqa: E501
                            with m.Else():
                                m.d.sync += self.b_input.eq(self.bus.dat_r[24:])  # noqa: E501
                        with m.Case(MemSel.HWORD):
                            with m.If(data_adr[1] == 0):
                                m.d.sync += self.b_input.eq(self.bus.dat_r[0:16])  # noqa: E501
                            with m.Else():
                                m.d.sync += self.b_input.eq(self.bus.dat_r[16:])  # noqa: E501
                        with m.Case(MemSel.WORD):
                            m.d.sync += self.b_input.eq(self.bus.dat_r)

        # Control conns
        m.d.comb += [
            self.control.opcode.eq(self.decode.opcode),
            self.control.requested_op.eq(self.decode.requested_op),
            self.control.e_type.eq(self.decode.e_type),
            self.req_next.eq(self.control.mem_req),
            self.insn_fetch_next.eq(self.control.insn_fetch),
            self.control.mem_valid.eq(self.bus.ack),

            # TODO: Spin out into a register of exception sources.
            self.control.exception.eq(self.decode.illegal)
        ]

        # An ACK stops the request b/c the microcode's to avoid a 1-cycle delay
        # due to registered REQ/FETCH signal.
        m.d.comb += [
            self.bus.cyc.eq(self.control.mem_req),
            self.bus.stb.eq(self.control.mem_req),
            # self.insn_fetch.eq(self.control.insn_fetch)
        ]

        m.d.comb += [
            self.datapath.gp.ctrl.reg_read.eq(self.control.gp.reg_read),
            self.datapath.gp.ctrl.reg_write.eq(self.control.gp.reg_write),
            self.datapath.pc.ctrl.action.eq(self.control.pc.action)
        ]

        # connect(m, self.datapath.gp.ctrl, self.control.gp)
        # connect(m, self.datapath.pc.ctrl, self.control.pc)

        write_data = Signal.like(self.bus.dat_w)
        with m.If(self.control.latch_data):
            # TODO: Misaligned accesses
            with m.Switch(self.control.mem_sel):
                with m.Case(MemSel.BYTE):
                    with m.If(data_adr[0:2] == 0):
                        m.d.sync += write_data[0:8].eq(self.alu.data.o[0:8])
                    with m.Elif(data_adr[0:2] == 1):
                        m.d.sync += write_data[8:16].eq(self.alu.data.o[0:8])
                    with m.Elif(data_adr[0:2] == 2):
                        m.d.sync += write_data[16:24].eq(self.alu.data.o[0:8])
                    with m.Else():
                        m.d.sync += write_data[24:].eq(self.alu.data.o[0:8])
                with m.Case(MemSel.HWORD):
                    with m.If(data_adr[1] == 0):
                        m.d.sync += write_data[0:16].eq(self.alu.data.o[0:16])
                    with m.Else():
                        m.d.sync += write_data[16:].eq(self.alu.data.o[0:16])
                with m.Case(MemSel.WORD):
                    m.d.sync += write_data.eq(self.alu.data.o)

        m.d.comb += [
            self.bus.we.eq(self.control.write_mem),
            self.bus.dat_w.eq(write_data),
            self.datapath.gp.dat_w.eq(self.alu.data.o),
            self.datapath.gp.adr_r.eq(self.reg_r_adr),
            self.datapath.gp.adr_w.eq(self.reg_w_adr),
            # FIXME: Compressed insns.
            self.datapath.pc.dat_w.eq(self.alu.data.o[2:]),
        ]

        with m.If(self.control.latch_adr):
            m.d.sync += data_adr.eq(self.alu.data.o)

        # DataPath.dat_w constantly has traffic. We only want to latch
        # the address once per mem access, and we want it the address to be
        # valid synchronous with ready assertion.
        with m.If(self.bus.cyc & self.bus.stb):
            with m.If(self.insn_fetch_next):
                m.d.comb += [self.bus.adr.eq(self.datapath.pc.dat_r),
                             self.bus.sel.eq(0xf)]
            with m.Else():
                m.d.comb += self.bus.adr.eq(data_adr[2:])

                # TODO: Misaligned accesses
                with m.Switch(self.control.mem_sel):
                    with m.Case(MemSel.BYTE):
                        with m.If(data_adr[0:2] == 0):
                            m.d.comb += self.bus.sel.eq(1)
                        with m.Elif(data_adr[0:2] == 1):
                            m.d.comb += self.bus.sel.eq(2)
                        with m.Elif(data_adr[0:2] == 2):
                            m.d.comb += self.bus.sel.eq(4)
                        with m.Else():
                            m.d.comb += self.bus.sel.eq(8)
                    with m.Case(MemSel.HWORD):
                        with m.If(data_adr[1] == 0):
                            m.d.comb += self.bus.sel.eq(3)
                        with m.Else():
                            m.d.comb += self.bus.sel.eq(0xc)
                    with m.Case(MemSel.WORD):
                        m.d.comb += self.bus.sel.eq(0xf)

        # Decode conns
        m.d.comb += [
            self.decode.insn.eq(self.bus.dat_r),
            # Decode begins automatically.
            self.decode.do_decode.eq(self.control.insn_fetch & self.bus.ack),
        ]

        with m.Switch(self.control.reg_r_sel):
            with m.Case(RegRSel.INSN_RS1):
                m.d.comb += self.reg_r_adr.eq(self.decode.src_a)
            with m.Case(RegRSel.INSN_RS2):
                m.d.comb += self.reg_r_adr.eq(self.decode.src_b)
            with m.Case(RegRSel.INSN_RS1_UNREGISTERED):
                m.d.comb += self.reg_r_adr.eq(self.decode.rs1)

        m.d.comb += self.reg_w_adr.eq(self.decode.dst)

        if self.formal:
            CHECK_INT_ADDR = 1
            EXCEPTION_HANDLER_ADDR = 224

            # rvfi_valid
            curr_upc = Signal.like(self.control.ucoderom.addr)
            prev_upc = Signal.like(curr_upc)

            # In general, if there's an insn_fetch and ACK, by the next cycle,
            # the insn has been retired. Handle exceptions to this rule on a
            # case-by-case basis.
            m.d.sync += [
                curr_upc.eq(self.control.ucoderom.addr),
                prev_upc.eq(curr_upc),
            ]

            # rvfi_insn/trap/rs1_addr/rs2_addr/rd_addr/rd_wdata/pc_rdata/
            # valid/order/rs1_data/rs2_data/pc_wdata/mem_addr/mem_rmask/
            # mem_wmask/mem_rdata/mem_wdata
            m.submodules.rvfi_rs1 = rs1_port = self.datapath.regfile.mem.read_port()
            m.submodules.rvfi_rs2 = rs2_port = self.datapath.regfile.mem.read_port()

            first_insn = Signal(1, reset=1)
            with m.FSM() as fsm:
                # There is nothing to pipeline/interleave, so wait for first
                # insn to be fetched.
                with m.State("FIRST_INSN"):
                    with m.If(self.control.insn_fetch & self.bus.ack):
                        m.next = "VALID_LATCH_DECODER"

                with m.State("VALID_LATCH_DECODER"):
                    m.d.comb += self.rvfi.valid.eq(1)
                    m.d.sync += [
                        self.rvfi.insn.eq(self.decode.insn),
                        self.rvfi.order.eq(self.rvfi.order + 1),
                        self.rvfi.trap.eq(self.decode.illegal),
                        self.rvfi.rs1_addr.eq(self.decode.rs1),
                        self.rvfi.rs2_addr.eq(self.decode.rs2),
                        # We have access to RD now, but RVFI mandates zero if
                        # no write. So wait until we know for sure.
                        self.rvfi.rd_addr.eq(0),
                        # Ditto.
                        self.rvfi.rd_wdata.eq(0),
                        self.rvfi.pc_rdata.eq(self.datapath.pc.dat_r << 2),
                    ]

                    with m.If(first_insn):
                        m.d.comb += self.rvfi.valid.eq(0)
                        m.d.sync += [
                            self.rvfi.order.eq(0),
                            first_insn.eq(0)
                        ]

                    # Prepare to read register file.
                    m.d.comb += [
                        rs1_port.addr.eq(self.decode.rs1),
                        rs2_port.addr.eq(self.decode.rs2)
                    ]

                    m.next = "WAIT_FOR_ACK"

                with m.State("WAIT_FOR_ACK"):
                    # Hold addresses in case we're here for a bit.
                    m.d.comb += [
                        rs1_port.addr.eq(self.decode.rs1),
                        rs2_port.addr.eq(self.decode.rs2)
                    ]

                    m.d.sync += [
                        self.rvfi.rs1_rdata.eq(rs1_port.data),
                        self.rvfi.rs2_rdata.eq(rs2_port.data),
                    ]

                    # And latch write data if there's a write.
                    with m.If(self.datapath.gp.ctrl.reg_write):
                        m.d.sync += [
                            self.rvfi.rd_wdata.eq(self.datapath.gp.dat_w),
                            self.rvfi.rd_addr.eq(self.datapath.gp.adr_w)
                        ]

                    # FIXME: Turn into a "peek" action for the PC reg, rather
                    # than duplicating PC logic here.
                    with m.If(self.datapath.pc.ctrl.action == PcAction.INC):
                        m.d.sync += self.rvfi.pc_wdata.eq(
                            (self.datapath.pc.dat_r + 1) << 2)
                    with m.Elif(self.datapath.pc.ctrl.action ==
                                PcAction.LOAD_ALU_O):
                        m.d.sync += self.rvfi.pc_wdata.eq(
                            self.datapath.pc.dat_w << 2)

                    with m.If(~self.control.insn_fetch & self.bus.ack):
                        m.d.sync += [
                            self.rvfi.mem_addr.eq(self.bus.adr << 2),
                            self.rvfi.mem_rdata.eq(self.bus.dat_r),
                            self.rvfi.mem_wdata.eq(self.bus.dat_w),
                        ]

                        with m.If(self.bus.we):
                            m.d.sync += [
                                self.rvfi.mem_rmask.eq(0),
                                self.rvfi.mem_wmask.eq(self.bus.sel)
                            ]
                        with m.Else():
                            m.d.sync += [
                                self.rvfi.mem_rmask.eq(self.bus.sel),
                                self.rvfi.mem_wmask.eq(0)
                            ]

                    with m.If(self.control.insn_fetch & self.bus.ack):
                        m.next = "VALID_LATCH_DECODER"

            # rvfi_intr
            with m.If(self.rvfi.valid):
                m.d.sync += self.rvfi.intr.eq(0)

            # FIXME: Imprecise/needs work.
            with m.If(curr_upc == EXCEPTION_HANDLER_ADDR):
                m.d.sync += self.rvfi.intr.eq(1)

            # rvfi_halt
            m.d.comb += self.rvfi.halt.eq(0)

            # rvfi_mode
            m.d.comb += self.rvfi.mode.eq(3)

            # rvfi_ixl
            m.d.comb += self.rvfi.ixl.eq(1)

        return m


class TopMem(Elaboratable):
    def __init__(self):

        self.top = Top()
