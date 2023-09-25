import enum as pyenum
from io import IOBase
from pathlib import Path
from itertools import tee, zip_longest

from amaranth import unsigned, Memory, Module
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import Signature, In, Out, Component
from amaranth.utils import log2_int
from m5pre import M5Pre
from m5meta import M5Meta


class UCodeROMControlGasket(Component):
    @property
    def signature(self):
        return Signature({
            "vec_adr": Out(self.ucoderom.fields.shape()["cond_test"].shape),
            "alu_op": Out(self.ucoderom.fields.shape()["alu_op"].shape),
            # "test": Out(self.ucoderom.fields.shape()["test"].shape),
            "pc_action": Out(self.ucoderom.fields.shape()["pc_action"].shape),
            "a_src": Out(self.ucoderom.fields.shape()["a_src"].shape),
            "b_src": Out(self.ucoderom.fields.shape()["b_src"].shape),
            "reg_op": Out(self.ucoderom.fields.shape()["reg_op"].shape),
            "mem_req": Out(self.ucoderom.fields.shape()["mem_req"].shape),
        })

    def __init__(self, ucoderom):
        self.ucoderom = ucoderom
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [
            self.vec_adr.eq(self.ucoderom.fields.cond_test),
            self.alu_op.eq(self.ucoderom.fields.alu_op),
            self.test.eq(self.ucoderom.fields.test),
            self.pc_action.eq(self.ucoderom.fields.pc_action),
            self.a_src.eq(self.ucoderom.fields.a_src),
            self.b_src.eq(self.ucoderom.fields.b_src),
            self.reg_op.eq(self.ucoderom.fields.reg_op),
            self.mem_req.eq(self.ucoderom.fields.mem_req),
        ]


class UCodeROM(Component):
    @property
    def signature(self):
        return Signature({
            "addr": Out(log2_int(self.ucode_mem.depth)),
            "fields": In(self.field_layout)
        }).flip()

    @staticmethod
    def main_microcode_file():
        return (Path(__file__).parent / "microcode.asm").resolve()

    def __init__(self, *, main_file=None, field_defs=None, hex=None):
        if not main_file:
            self.main_file = UCodeROM.main_microcode_file()
        else:
            self.main_file = main_file
        self.field_defs = field_defs
        self.hex = hex

        self.assemble()
        self.ucode_mem = Memory(width=self.width, depth=self.depth,
                                init=self.ucode_contents)
        super().__init__()

    def elaborate(self, platform):
        m = Module()
        m.submodules.rdport = rdport = \
            self.ucode_mem.read_port(transparent=False)

        m.d.comb += [
            rdport.addr.eq(self.addr),
            self.fields.as_value().eq(rdport.data)
        ]

        return m

    # Like M5Meta.assemble(), but pass3 is more flexible and tailored to my
    # needs.
    def assemble(self):
        if isinstance(self.main_file, IOBase):
            self.m5meta = M5Meta(self.main_file, obj_base_fn="anonymous")
            self.m5meta.src = M5Pre(self.main_file).read()
        else:
            with open(self.main_file) as mfp:
                self.m5meta = M5Meta(mfp, obj_base_fn=self.main_file.stem)
                self.m5meta.src = M5Pre(mfp).read()

        passes = [None,
                  self.m5meta.pass12,
                  self.m5meta.pass12]

        for p in range(1, len(passes)):
            self.m5meta.pass_num = p
            passes[p]()

        if len(self.m5meta.spaces) != 1:
            raise ValueError("UCodeROM does not support multiple microcode address spaces")  # noqa: E501

        # pass3- Create enums and signals for amaranth code. Optionally
        # generate extra files for debugging.
        space = next(iter(self.m5meta.spaces.values()))
        # assert(space.name == "block_ram")
        space.generate_object()

        self.create_mem_init(space)
        self.create_field_layout(space)

        if self.hex:
            space.write_hex_file(self.hex)

        if self.field_defs:
            with open(self.field_defs, 'w') as f:
                space.write_fdef(f)

    def create_mem_init(self, space):
        self.width = space.width
        self.depth = space.size
        self.ucode_contents = [0]*self.depth

        # Pre-filled with zeros. Fill in addresses that m5meta claims to
        # contain data by converting the address to an int (a dictionary
        # is used to represent address space holes implicitly).
        for addr in sorted(space.data.keys()):
            self.ucode_contents[int(addr)] = space.data[addr]

    def create_field_layout(self, space):
        layout = dict()
        padding_id = 0

        c, n = tee(space.fields.items())
        next(n, None)
        curr_next_pairs = zip_longest(c, n, fillvalue=(None, None))

        for (curr_n, curr_f), (_, next_f) in curr_next_pairs:
            if curr_f.enum:
                nice_keys = {k.upper(): v for k, v in curr_f.enum.items()}
                layout[curr_n] = pyenum.Enum(curr_n, nice_keys)
            else:
                layout[curr_n] = unsigned(curr_f.width)

            if next_f and curr_f.origin + curr_f.width != next_f.origin:
                layout[f"_padding_{padding_id}"] = \
                    unsigned(next_f.origin - (curr_f.origin + curr_f.width))

        self.field_layout = StructLayout(layout)


# Helper class to propogate dynamically-generated enum classes from
# microcode file to components that depend on these classes.
class UCodeFieldClasses:
    def __init__(self, layout_or_fn):
        self.shapes = dict()

        # We already have an appropriate layout.
        if isinstance(layout_or_fn, StructLayout):
            self.map_field_layout(layout_or_fn)
        # Load microcode classes from given file.
        elif isinstance(layout_or_fn, str):
            self.map_field_layout(UCodeROM(main_file=layout_or_fn)
                                  .field_layout)
        # Attempt default from this repo.
        elif not layout_or_fn:
            self.map_field_layout(UCodeROM().field_layout)
        else:
            raise ValueError("Could not extract dynamically-generated"
                             f"microcode classes from {type(layout_or_fn)}.\n"
                             "If using \"amaranth generate\" set the \"ucode\""
                             "(or similar) parameter to \"\"")

    def map_field_layout(self, layout):
        for k, v in layout:
            self.shapes[k] = v.shape

    def __getitem__(self, key):
        return self.shapes[key]
