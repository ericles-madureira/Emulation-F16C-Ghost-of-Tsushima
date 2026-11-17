"""F16C -> AVX: software replacements for VCVTPH2PS and VCVTPS2PH.

F16C has exactly two instructions:
    VCVTPH2PS xmm/ymm, xmm/m64/m128         half (16-bit) -> float (32-bit)
    VCVTPS2PH xmm/m64/m128, xmm/ymm, imm8   float (32-bit) -> half (16-bit)

lower(ins, ip, pool) returns machine code that does the same thing with plain
AVX (no AVX2, no FMA). The code is meant to be placed at address ``ip`` and ends
with a JMP back to the instruction that followed the original one.

Speed: every lane is converted at once with branch-free SIMD code (4 lanes per
128-bit step), and no general register or flag is touched, so nothing of that
kind needs saving. Vector registers that the program overwrites right after the
instruction without reading them first ("dead", see ``vector_liveness``) are
used as scratch for free; any other scratch register is saved on the stack and
restored (only its low half when the upper half is dead). When nothing needs saving the replacement uses no stack at all.
VCVTPS2PH only reads MXCSR when the result can depend on MXCSR.DAZ (directed
or MXCSR rounding); round-to-nearest and truncation skip it. Constants come
from a shared pool (``POOL``) that the patcher places once in the new section.

Exactness: the results are bit-identical to the real instructions, in all four
rounding modes (fixed or taken from MXCSR.RC), with infinities, NaN (made
quiet), subnormals and MXCSR.DAZ. Every floating-point step used is exact, so
the result does not depend on MXCSR, except for VCVTPS2PH's "round as MXCSR
says" form, which rounds with VROUNDPS in MXCSR mode, like the real thing.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pylibs"))
import iced_x86 as ix

R, C, I, M, O = ix.Register, ix.Code, ix.Instruction, ix.MemoryOperand, ix.OpKind

GPR32 = set(range(R.EAX, R.R15D + 1))

# Shared constants: 16 bytes each (the same dword in all 4 lanes).
_CONSTS = {
    "h_mag": 0x00007FFF, "h_bias": 0x38000000, "h_maxfin": 0x00007BFF, "h_inf": 0x00007C00,
    "f_quiet": 0x00400000, "h_submax": 0x000003FF, "f_2m24": 0x33800000,
    "f_sign": 0x80000000, "f_submax": 0x387FFFFF, "e_2p24": 0x0C000000, "f_mant": 0x007FFFFF,
    "daz": 0x00000040, "f_1024": 0x44800000, "h_base": 0x0001C400, "f_ovf": 0x477FFFFF,
    "f_1024p75": 0x44801800, "h_ovbase": 0x000077FF,
    "f_maxfin": 0x7F7FFFFF, "f_inf": 0x7F800000, "h_quiet": 0x00000200, "zero": 0,
}
_NAMES = list(_CONSTS)
POOL = b"".join(struct.pack("<4I", *[_CONSTS[n]] * 4) for n in _NAMES)
POOL_ALIGN = 16


class LoweringError(Exception):
    pass


def is_f16c(ins: ix.Instruction) -> bool:
    return (ins.mnemonic in (ix.Mnemonic.VCVTPH2PS, ix.Mnemonic.VCVTPS2PH)
            and ins.encoding == ix.EncodingKind.VEX
            and ix.CpuidFeature.F16C in ins.cpuid_features())


class _Asm:
    def __init__(self):
        self.instructions, self.labels, self.branches = [], {}, []

    def emit(self, ins):
        self.instructions.append(ins)

    def label(self, name):
        self.labels[name] = len(self.instructions)

    def jump(self, code, label):
        self.branches.append((len(self.instructions), label))
        self.emit(I.create_branch(code, 0))

    def encode(self, ip):
        base = 0x123400000000          # fake unique addresses so labels resolve
        for n, ins in enumerate(self.instructions):
            ins.ip = base + n * 16
        for n, label in self.branches:
            self.instructions[n].near_branch64 = base + self.labels[label] * 16
        enc = ix.BlockEncoder(64)
        enc.add_many(self.instructions)
        return enc.encode(ip)


def _signed64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


# Instructions that use vector registers in ways the register list does not show.
_OPAQUE = {getattr(ix.Mnemonic, n) for n in (
    "FXSAVE", "FXSAVE64", "FXRSTOR", "FXRSTOR64", "XSAVE", "XSAVE64", "XSAVEOPT", "XSAVEOPT64", "XSAVEC",
    "XSAVEC64", "XSAVES", "XSAVES64", "XRSTOR", "XRSTOR64", "XRSTORS", "XRSTORS64", "SYSCALL", "INT", "INT3", "INT1", "INTO", "UD0", "UD1", "UD2", "HLT") if hasattr(ix.Mnemonic, n)}
_INFO = ix.InstructionInfoFactory()
_WRITE_ONLY = ix.OpAccess.WRITE


def _vec_number(reg):
    for first in (R.XMM0, R.YMM0, R.ZMM0):
        if first <= reg <= first + 31:
            return reg - first
    return None


def _effects(ins):
    """(read, written) items of one instruction. Item n < 16 is the low 128 bits of
    XMMn, item 16 + n its upper 128 bits."""
    if ins.mnemonic == ix.Mnemonic.VZEROUPPER:          # clears every upper half, reads nothing
        return frozenset(), frozenset(range(16, 32))
    if ins.mnemonic == ix.Mnemonic.VZEROALL:
        return frozenset(), frozenset(range(32))
    reads, writes = set(), set()
    vex = ins.encoding in (ix.EncodingKind.VEX, ix.EncodingKind.EVEX)
    for u in _INFO.info(ins).used_registers():
        num = _vec_number(u.register)
        if num is None or num >= 16:
            continue
        whole = u.register >= R.YMM0
        if u.access == _WRITE_ONLY:
            writes.add(num)
            # A VEX/EVEX write sets or clears the upper bits; a legacy SSE write keeps them.
            if vex or whole:
                writes.add(num + 16)
        else:                                   # any kind of read, or a write that may not happen
            reads.add(num)
            if whole:
                reads.add(num + 16)
    return frozenset(reads), frozenset(writes)


_NEAR = (O.NEAR_BRANCH16, O.NEAR_BRANCH32, O.NEAR_BRANCH64)


def vector_liveness(code, ip: int, limit: int = 400, cache: dict | None = None) -> tuple[frozenset, frozenset]:
    """Which vector registers the program no longer needs at ``ip``.

    ``code`` is either the program's bytes starting at ``ip`` or a function
    ``fetch(address) -> bytes`` (empty outside the program's code). ``ip`` is the
    address right after the converted instruction. Returns (dead, upper_dead),
    sets of XMM0-XMM15:
      dead:        the whole 256-bit register is overwritten before any part of it is read;
      upper_dead:  at least the upper 128 bits are (a VEX/EVEX write clears them, and the
                   code before it reads only the low half).
    Every path that follows is examined, through direct jumps, both sides of
    conditional jumps and loops, for up to ``limit`` instructions. A path stops
    at a call, return, indirect jump or an instruction whose vector register use
    is not fully described, and whatever it has not settled by then counts as
    live. So does everything when the limit is reached.
    The replacement code uses dead registers without saving them and saves only
    the low half of the upper-dead ones. ``cache``: decoded instructions, shared
    between calls on the same program."""
    if isinstance(code, (bytes, bytearray, memoryview)):
        blob, origin = bytes(code), ip
        fetch = lambda a: blob[a - origin:a - origin + 15] if origin <= a < origin + len(blob) else b""
    else:
        fetch = code
    cache = {} if cache is None else cache

    def step(addr):
        if addr not in cache:
            data = fetch(addr)
            ins = ix.Decoder(64, bytes(data[:15]), ip=addr).decode() if data else None
            if ins is None or ins.is_invalid or ins.mnemonic in _OPAQUE:
                cache[addr] = None
            else:
                fc = ins.flow_control
                follow = ()
                if fc == ix.FlowControl.NEXT:
                    follow = (ins.next_ip,)
                elif fc == ix.FlowControl.UNCONDITIONAL_BRANCH and ins.op0_kind in _NEAR:
                    follow = (ins.near_branch_target,)
                elif fc == ix.FlowControl.CONDITIONAL_BRANCH and ins.op0_kind in _NEAR:
                    follow = (ins.next_ip, ins.near_branch_target)
                cache[addr] = _effects(ins) + (follow,)
        return cache[addr]

    live, seen = set(), {}
    work, budget = [(ip, frozenset(range(32)))], limit
    while work:
        addr, open_ = work.pop()
        # Whether an item is read before being overwritten, from a given address on,
        # does not depend on how the path got there: each (address, item) is walked once.
        # Arriving again (a loop) adds nothing.
        open_ = open_ - live - seen.get(addr, frozenset())
        if not open_:
            continue
        seen[addr] = seen.get(addr, frozenset()) | open_
        info = step(addr) if budget > 0 else None
        budget -= 1
        if info is None:
            live |= open_
            continue
        reads, writes, follow = info
        live |= open_ & reads
        open_ = open_ - reads - writes
        if open_ and not follow:                # call, return, indirect jump...
            live |= open_
        elif open_:
            work.extend((target, open_) for target in follow)
    upper_dead = {n for n in range(16) if n + 16 not in live}
    dead = {n for n in upper_dead if n not in live}
    return frozenset(R.XMM0 + n for n in dead), frozenset(R.XMM0 + n for n in upper_dead)


def _is_ymm(reg):
    return R.YMM0 <= reg <= R.YMM15


def _xmm(reg):
    if R.XMM0 <= reg <= R.XMM15:
        return reg
    if _is_ymm(reg):
        return R.XMM0 + (reg - R.YMM0)
    raise LoweringError(f"unexpected register {reg}")


def _ymm(xmm):
    return R.YMM0 + (xmm - R.XMM0)


def _effective_address(ins, frame, extra=0):
    """The instruction's memory operand (+extra bytes), valid after RSP was lowered by frame."""
    if ins.segment_prefix in (R.FS, R.GS):
        raise LoweringError("FS/GS-relative memory operand")
    if ins.is_ip_rel_memory_operand:
        if ins.memory_base != R.RIP:
            raise LoweringError("EIP-relative memory operand")
        return M(R.RIP, displ=ins.ip_rel_memory_address + extra, displ_size=8)
    base, index = ins.memory_base, ins.memory_index
    disp = _signed64(ins.memory_displacement) + extra
    if base in (R.RSP, R.ESP):
        disp += frame
    if base == R.NONE and index == R.NONE:
        return M(displ=disp, displ_size=ins.memory_displ_size if ins.memory_displ_size in (4, 8) else 4)
    addr32 = base in GPR32 or index in GPR32
    if addr32:                                             # 32-bit addressing wraps
        disp = ((disp + 0x80000000) & 0xFFFFFFFF) - 0x80000000
    elif not -0x80000000 <= disp <= 0x7FFFFFFF:
        raise LoweringError("displacement out of range after stack adjustment")
    size = 1 if -128 <= disp <= 127 else (4 if addr32 else 8)
    return M(base, index, ins.memory_index_scale, disp, size)


def _sp(disp):
    return M(R.RSP, displ=disp, displ_size=1 if -128 <= disp <= 127 else 8)


class _Stub:
    """Builds one replacement: stack frame, scratch-register save/restore, return jump."""

    def __init__(self, ins, pool, avoid, scratch, mxcsr=False, dead=(), upper_dead=()):
        self.ins, self.pool, self.a = ins, pool, _Asm()
        free = [r for r in range(R.XMM0, R.XMM15 + 1) if r not in avoid]
        if len(free) < scratch:
            raise LoweringError("not enough free vector registers")
        # Cheapest first: dead registers cost nothing, upper-dead ones a 16-byte save.
        free.sort(key=lambda r: 0 if r in dead else 1 if r in upper_dead else 2)
        self.regs = free[:scratch]
        # (register, stack offset, bytes): whole YMM first, then low halves only.
        live = sorted((r for r in self.regs if r not in dead), key=lambda r: r in upper_dead)
        self.saved, offset = [], 0
        for r in live:
            size = 16 if r in upper_dead else 32
            self.saved.append((r, offset, size))
            offset += size
        # [mxcsr]: copy of MXCSR; 16 bytes later: the denormal threshold built from it
        self.mxcsr = offset if mxcsr else None
        self.frame = offset + (32 if mxcsr else 0)
        self.e = self.a.emit

    def k(self, name):
        return M(R.RIP, displ=self.pool + 16 * _NAMES.index(name), displ_size=8)

    def mem(self, extra=0):
        return _effective_address(self.ins, self.frame, extra)

    def prologue(self):
        if self.frame:
            self.e(I.create_reg_mem(C.LEA_R64_M, R.RSP, _sp(-self.frame)))
        for reg, at, size in self.saved:            # full YMM unless the upper half is dead:
            if size == 32:                          # VEX.128 writes clear the upper half
                self.e(I.create_mem_reg(C.VEX_VMOVUPS_YMMM256_YMM, _sp(at), _ymm(reg)))
            else:
                self.e(I.create_mem_reg(C.VEX_VMOVUPS_XMMM128_XMM, _sp(at), reg))
        if self.mxcsr is not None:
            # threshold = MXCSR.DAZ ? 0x7FFFFF : 0. Floats whose bits are <= it count as zero.
            t = self.regs[0]
            self.e(I.create_mem(C.VEX_VSTMXCSR_M32, _sp(self.mxcsr)))
            self.e(I.create_reg_mem(C.VEX_VBROADCASTSS_XMM_M32, t, _sp(self.mxcsr)))
            PAND(self, t, t, "daz")
            self.e(I.create_reg_reg_mem(C.VEX_VPCMPEQD_XMM_XMM_XMMM128, t, t, self.k("daz")))
            PAND(self, t, t, "f_mant")
            self.e(I.create_mem_reg(C.VEX_VMOVUPS_XMMM128_XMM, self.threshold, t))

    @property
    def threshold(self):
        return None if self.mxcsr is None else _sp(self.mxcsr + 16)

    def epilogue(self):
        for reg, at, size in self.saved:
            if size == 32:
                self.e(I.create_reg_mem(C.VEX_VMOVUPS_YMM_YMMM256, _ymm(reg), _sp(at)))
            else:
                self.e(I.create_reg_mem(C.VEX_VMOVUPS_XMM_XMMM128, reg, _sp(at)))
        if self.frame:
            self.e(I.create_reg_mem(C.LEA_R64_M, R.RSP, _sp(self.frame)))
        # The NOP keeps Windows' unwinder from reading "LEA RSP; JMP" as a function
        # epilogue (a tail call), which this is not.
        self.e(I.create(C.NOPD))
        self.e(I.create_branch(C.JMP_REL32_64, self.ins.next_ip))

    # three-operand helpers: op(dst, src1, src2); src2 is a register, a constant name or memory
    def op(self, reg_code, mem_code, dst, s1, s2):
        if isinstance(s2, str):
            self.e(I.create_reg_reg_mem(mem_code, dst, s1, self.k(s2)))
        elif isinstance(s2, M):
            self.e(I.create_reg_reg_mem(mem_code, dst, s1, s2))
        else:
            self.e(I.create_reg_reg_reg(reg_code, dst, s1, s2))


def _op3(name):                         # the register and memory forms share one Code
    code = getattr(C, f"VEX_{name}_XMM_XMM_XMMM128")
    return lambda s, d, a, b: s.op(code, code, d, a, b)


PAND, POR, PXOR, PANDN = _op3("VPAND"), _op3("VPOR"), _op3("VPXOR"), _op3("VPANDN")
PADDD, PSUBD, PCMPGTD = _op3("VPADDD"), _op3("VPSUBD"), _op3("VPCMPGTD")
MULPS, XORPS = _op3("VMULPS"), _op3("VXORPS")
PACKUSDW = _op3("VPACKUSDW")


def _shift(s, code, dst, src, n):
    s.e(I.create_reg_reg_i32(code, dst, src, n))


def _blend(s, dst, if_clear, if_set, mask):
    """dst = mask ? if_set : if_clear (per lane, sign bit of mask)."""
    if isinstance(if_set, str):
        s.e(I.create_reg_reg_mem_reg(C.VEX_VBLENDVPS_XMM_XMM_XMMM128_XMM, dst, if_clear, s.k(if_set), mask))
    else:
        s.e(I.create_reg_reg_reg_reg(C.VEX_VBLENDVPS_XMM_XMM_XMMM128_XMM, dst, if_clear, if_set, mask))


def _unary(s, code, dst, src):
    s.e(I.create_reg_reg(code, dst, src))


def _half4_to_float(s, x, a, b, c):
    """x: 4 halves zero-extended to dwords -> 4 floats in x. a, b, c: scratch."""
    PAND(s, a, x, "h_mag")                        # a = exponent and mantissa
    PXOR(s, x, x, a)                              # x = sign (bit 15)
    _shift(s, C.VEX_VPSLLD_XMM_XMM_IMM8, x, x, 16)
    _shift(s, C.VEX_VPSLLD_XMM_XMM_IMM8, b, a, 13)
    PADDD(s, b, b, "h_bias")                      # normal: exponent + (127 - 15)
    PCMPGTD(s, c, a, "h_maxfin")                  # infinity / NaN: exponent all ones
    PAND(s, c, c, "h_bias")
    PADDD(s, b, b, c)
    PCMPGTD(s, c, a, "h_inf")                     # NaN: made quiet
    PAND(s, c, c, "f_quiet")
    POR(s, b, b, c)
    _unary(s, C.VEX_VCVTDQ2PS_XMM_XMMM128, c, a)  # subnormal/zero: mantissa * 2^-24, exact
    MULPS(s, c, c, "f_2m24")
    PCMPGTD(s, a, a, "h_submax")
    _blend(s, b, c, b, a)
    POR(s, x, x, b)


def _ph2ps(ins, pool, dead, upper_dead):
    dst = ins.op0_register
    d = _xmm(dst)
    wide = _is_ymm(dst)
    mem = ins.op1_kind == O.MEMORY
    src = None if mem else _xmm(ins.op1_register)
    s = _Stub(ins, pool, {d, src}, 4 if wide else 3, dead=dead, upper_dead=upper_dead)
    s.prologue()
    a, b, c = s.regs[:3]
    if wide:
        lo = s.regs[3]
        if mem:
            s.e(I.create_reg_mem(C.VEX_VPMOVZXWD_XMM_XMMM64, lo, s.mem()))
            s.e(I.create_reg_mem(C.VEX_VPMOVZXWD_XMM_XMMM64, d, s.mem(8)))
        else:
            _unary(s, C.VEX_VPMOVZXWD_XMM_XMMM64, lo, src)
            _shift(s, C.VEX_VPSHUFD_XMM_XMMM128_IMM8, d, src, 0xEE)
            _unary(s, C.VEX_VPMOVZXWD_XMM_XMMM64, d, d)
        _half4_to_float(s, d, a, b, c)
        _half4_to_float(s, lo, a, b, c)
        s.e(I.create_reg_reg_reg_i32(C.VEX_VINSERTF128_YMM_YMM_XMMM128_IMM8, dst, _ymm(lo), d, 1))
    else:
        # A VEX.128 write clears the upper half of the YMM register, like the original.
        if mem:
            s.e(I.create_reg_mem(C.VEX_VPMOVZXWD_XMM_XMMM64, d, s.mem()))
        else:
            _unary(s, C.VEX_VPMOVZXWD_XMM_XMMM64, d, src)
        _half4_to_float(s, d, a, b, c)
    s.epilogue()
    return s


def _float4_to_half(s, src, out, regs, rounding):
    """src: 4 floats -> out: 4 halves in the low word of each dword.

    Each lane is scaled so that the half code's unit is 1.0, rounded once by
    VROUNDPS (exact, in the requested mode, with the sign so directed modes go
    the right way), and added to the exponent part of the code:
      normal half:     scaled = 1.mantissa * 2^10 (exponent field replaced), in [1024, 2048)
      subnormal half:  scaled = |x| * 2^24, in [0, 1024)
      overflow:        scaled = 1024.75 on top of the largest finite: rounds to max or infinity
    Rounding up to the next power of two carries into the exponent, as it should.
    src is only read before out is first written, so out may be src."""
    sg, a, nm, t, u = regs
    q = out
    PAND(s, sg, src, "f_sign")
    PXOR(s, a, src, sg)                           # a = |x| bits
    PCMPGTD(s, nm, a, "f_submax")                 # lanes that are a normal half or larger
    # |x| * 2^24 as an integer add to the exponent: floating-point math on a denormal
    # costs a slow microcode assist. A denormal float becomes tiny (rounds the same
    # way), or 0 when MXCSR.DAZ is set, as in the real instruction. Rounding to
    # nearest or toward zero turns "tiny" (and zero, which becomes 2^-103) into 0
    # anyway, so then DAZ cannot change the result and MXCSR is not read at all.
    PADDD(s, t, a, "e_2p24")
    if s.threshold is not None:
        PCMPGTD(s, u, a, s.threshold)
        PAND(s, t, t, u)
    PAND(s, u, a, "f_mant")
    POR(s, u, u, "f_1024")
    _blend(s, t, t, u, nm)
    PAND(s, q, a, "f_inf")                        # exponent part of the code (normal lanes)
    _shift(s, C.VEX_VPSRLD_XMM_XMM_IMM8, q, q, 13)
    PSUBD(s, q, q, "h_base")
    PAND(s, q, q, nm)
    PCMPGTD(s, u, a, "f_ovf")                     # |x| >= 65536
    _blend(s, t, t, "f_1024p75", u)
    _blend(s, q, q, "h_ovbase", u)
    XORPS(s, t, t, sg)
    s.e(I.create_reg_reg_i32(C.VEX_VROUNDPS_XMM_XMMM128_IMM8, t, t, rounding))
    XORPS(s, t, t, sg)
    _unary(s, C.VEX_VCVTTPS2DQ_XMM_XMMM128, t, t)
    PADDD(s, q, q, t)
    PCMPGTD(s, u, a, "f_maxfin")                  # infinity / NaN
    PCMPGTD(s, nm, a, "f_inf")                    # NaN: keep top mantissa bits, made quiet
    PAND(s, nm, nm, "h_quiet")
    _shift(s, C.VEX_VPSRLD_XMM_XMM_IMM8, a, a, 13)
    PAND(s, a, a, "h_submax")
    POR(s, a, a, nm)
    POR(s, a, a, "h_inf")
    _blend(s, q, q, a, u)
    _shift(s, C.VEX_VPSRLD_XMM_XMM_IMM8, sg, sg, 16)
    POR(s, q, q, sg)


def _ps2ph(ins, pool, dead, upper_dead):
    src = ins.op1_register
    sx = _xmm(src)
    wide = _is_ymm(src)
    mem = ins.op0_kind == O.MEMORY
    d = None if mem else ins.op0_register
    imm = ins.immediate8
    rounding = (0x4 if imm & 4 else imm & 3) | 0x8          # 8: no precision exception
    # MXCSR.DAZ only matters when a denormal can round away from zero: directed or MXCSR rounding.
    needs_daz = bool(imm & 4) or (imm & 3) in (1, 2)
    # The destination register doubles as the result register; a memory form needs one more.
    s = _Stub(ins, pool, {sx, d}, 5 + wide + mem, mxcsr=needs_daz, dead=dead, upper_dead=upper_dead)
    s.prologue()
    work = s.regs[:5]
    out = s.regs[5 + wide] if mem else d
    if wide:
        hi = s.regs[5]
        s.e(I.create_reg_reg_i32(C.VEX_VEXTRACTF128_XMMM128_YMM_IMM8, hi, src, 1))   # before out is written
        _float4_to_half(s, sx, out, work, rounding)
        _float4_to_half(s, hi, hi, work, rounding)
        PACKUSDW(s, out, out, hi)
    else:
        # A VEX.128 write clears the upper half of the YMM register, like the original.
        _float4_to_half(s, sx, out, work, rounding)
        PACKUSDW(s, out, out, "zero")
    if mem:
        store = C.VEX_VMOVDQU_XMMM128_XMM if wide else C.VEX_VMOVQ_XMMM64_XMM
        s.e(I.create_mem_reg(store, s.mem(), out))
    s.epilogue()
    return s


def lower(ins: ix.Instruction, ip: int, pool: int | None = None, dead=(), upper_dead=()) -> dict:
    """Replacement code for one F16C instruction, to be placed at ``ip``.

    pool: address where ``POOL`` was placed. When None, a private copy of the
    pool is appended to the code (useful for tests).
    dead, upper_dead: from ``vector_liveness``. Dead registers are used as
    scratch without being saved; for upper-dead ones only the low half is saved.

    Returns {"code", "prolog", "tail", "frame"}: the bytes, the length of the
    first instruction (the stack allocation; 0 when no stack is used), the offset
    just after the stack is released and the number of stack bytes used. The patcher uses these to
    describe the stack to Windows' unwinder."""
    if not is_f16c(ins):
        raise LoweringError(f"not an F16C instruction: {ins}")
    build = _ph2ps if ins.mnemonic == ix.Mnemonic.VCVTPH2PS else _ps2ph
    dead = frozenset(dead)
    upper_dead = frozenset(upper_dead) | dead
    private = pool is None
    if private:
        # RIP-relative operands are always 4 bytes, so the size does not depend on the target.
        size = len(build(ins, ip + 0x10000, dead, upper_dead).a.encode(ip))
        pool = (ip + size + POOL_ALIGN - 1) & -POOL_ALIGN
    stub = build(ins, pool, dead, upper_dead)
    code = stub.a.encode(ip)
    decoded = list(ix.Decoder(64, code, ip=ip))
    first, nop, last = decoded[0], decoded[-2], decoded[-1]
    if any(d.is_invalid for d in decoded) or sum(d.len for d in decoded) != len(code):
        raise LoweringError("generated code does not decode cleanly")
    if ((stub.frame and first.mnemonic != ix.Mnemonic.LEA) or last.code != C.JMP_REL32_64
            or last.near_branch_target != ins.next_ip or nop.mnemonic != ix.Mnemonic.NOP):
        raise LoweringError("generated code has an unexpected shape")
    for d in decoded:
        feats = d.cpuid_features()
        if any(f in feats for f in (ix.CpuidFeature.F16C, ix.CpuidFeature.AVX2, ix.CpuidFeature.FMA)):
            raise LoweringError("generated code still needs F16C/AVX2/FMA")
    result = {"code": code, "prolog": first.len if stub.frame else 0, "tail": nop.ip - ip, "frame": stub.frame,
              "saved": len(stub.saved)}
    if private:
        result["code"] = code + b"\xcc" * (pool - ip - len(code)) + POOL
    return result
