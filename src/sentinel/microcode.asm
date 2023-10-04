space block_ram: width 32, size 256;

space block_ram;
origin 0;

fields block_ram: {
  // Target field for direct jmp_type. The micropc jumps to here next
  // cycle if the test succeeds.
  target: width 8, origin 0, default 0;

  // Various jump types to jump around the microcode program next cycle.
  // cont: Increment upc by 1.
  // nop: Same as cont, but indicate we are using the target field for
  //      something else.
  // map: Use address supplied by opcode if test fails. Otherwise, unconditional
  //      direct.
  // direct: Conditionally use address supplied by target field. Otherwise,
  //         cont.
  // map_funct: Unconditionally jump to address supplied by target field
  //            plus an offset based on the current minor opcode.
  //            See "requested_op" signal.
  // direct_zero: Conditionally use address supplied by target field. Otherwise,
  //              0.
  jmp_type: enum { cont = 0; nop = 0; map; direct; map_funct; direct_zero; }, default cont;

  // Various tests (valid current cycle) for conditional jumps:
  // false: Unconditionally fail
  // int: Is interrupt line high?
  // exception: Illegal insn, EBRAK, ECALL, misaligned insn, misaligned ld/st?
  // mem_valid: Is current dat_r valid? Did write finish?
  // alu_ready: Is alu_ready (mainly for shifts)?
  // true: Unconditionally succeed
  cond_test: enum { false = 0; intr; exception; cmp_okay; mem_valid; true}, default true;

  // Invert the results of the test above. Valid current cycle.
  invert_test: bool, default 0;

  // Modify the PC for the next cycle.
  pc_action: enum { hold = 0; inc; load; }, default hold;

  // Latch the A input into the ALU. A input contents vaid next cycle.
  a_src: enum { gp = 0; pc; }, default gp;

  // Latch the B input into the ALU. B input contents vaid next cycle.
  // Target is for shifts.
  b_src: enum { gp = 0; imm; target; }, default gp;

  // Enum layout needs to match ALU.OpType
  alu_op: enum { add = 0; sub; and; or; xor; sll; srl; sra; cmp_eq; cmp_ne; cmp_lt; cmp_ltu; cmp_ge; cmp_geu; nop }, default nop;

  // Either read or write a register in the register file. _Which_ register
  // to read/write comes either from the decoded insn or from microcode inputs.
  // Read contents will be on the data bus the next cycle. Written contents
  // will be valid on the next cycle. Reads are NOT transparent.
  reg_op: enum { none = 0; read_a; read_b_latch_a; latch_b; write_dst; }, default none;

  // Start or continue a memory request. For convenience, an ack will
  // automatically stop a memory request for the cycle after ack, even if
  // mem_req is enabled. Valid on current cycle.
  mem_req: bool, default 0;

  // Current mem request is insn fetch. Valid on current cycle.
  insn_fetch: bool, default 0;
};

#define INSN_FETCH insn_fetch => 1, mem_req => 1
#define SKIP_WAIT_IF_ACK jmp_type => direct_zero, cond_test => mem_valid, target => skip_wait_for_ack
#define JUMP_TO_OP_END(trg) cond_test => true, jmp_type => direct, target => trg

fetch:
wait_for_ack: INSN_FETCH, invert_test => 1, cond_test => mem_valid, \
                  jmp_type => direct, target => wait_for_ack;
skip_wait_for_ack: reg_op => read_a;
              // Illegal insn or insn misaligned exception possible
check_int:    jmp_type => map, reg_op => read_b_latch_a, cond_test => exception, target => save_pc;

origin 8;
imm_prolog: reg_op => latch_b, b_src => imm, pc_action => inc, jmp_type => map_funct, target => imm_ops;
reg_prolog: reg_op => latch_b, b_src => gp, pc_action => inc, jmp_type => map_funct, target => reg_ops;

imm_ops:
addi:
              alu_op => add, INSN_FETCH, JUMP_TO_OP_END(fast_epilog);
// Trampolines for multicycle ops are almost zero-cost except for microcode space.
slli_trampoline:
              alu_op => sll, jmp_type => direct, target => slli;

slli:
              // Need 3-way jump! alu_op => sll, jmp_type => direct, cond_test => alu_ready, target => imm_ops_end;
              alu_op => sll, jmp_type => direct, cond_test => alu_ready, invert_test => true, target => slli;
              alu_op => sll, INSN_FETCH, JUMP_TO_OP_END(fast_epilog); // Hold ALU's results by keeping alu_op the same.

reg_ops:
add:
              alu_op => add, INSN_FETCH, JUMP_TO_OP_END(fast_epilog);

fast_epilog:
              reg_op => write_dst, INSN_FETCH, SKIP_WAIT_IF_ACK;

// Interrupt handler.
origin 224;
// Send PC through ALU
save_pc: a_src => pc, b_src => target, jmp_type => nop, target => 0;
