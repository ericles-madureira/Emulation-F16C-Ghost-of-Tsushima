"""Runs the generated replacement code on this CPU and compares it with a
software reference written independently (exact fractions).

Works on any CPU with AVX, with or without F16C:
    python\\python.exe tests\\test_lower.py
"""
import ctypes
import random
import struct
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "core" / "pylibs"), str(ROOT / "core")]
import iced_x86 as ix
import f16c_lower

R, C, I, M = ix.Register, ix.Code, ix.Instruction, ix.MemoryOperand
GPRS = [R.RAX, R.RCX, R.RDX, R.RBX, R.RSI, R.RDI, R.R8, R.R9]
FLAG_MASK = 0x8D5  # OF SF ZF AF PF CF


# ------------------------------------------------------------ reference
def half_value(code):
    exp, mant = (code >> 10) & 31, code & 0x3FF
    if exp == 0:
        return Fraction(mant, 1 << 24)
    return Fraction(0x400 | mant, 1 << 10) * Fraction(2) ** (exp - 15)


HALF_VALUES = [half_value(c) for c in range(0x7C00)] + [Fraction(65536)]


def ref_ph2ps(h):
    sign, exp, mant = h >> 15, (h >> 10) & 31, h & 0x3FF
    if exp == 31:
        return sign << 31 | 0x7F800000 | ((0x400000 | mant << 13) if mant else 0)
    bits = struct.unpack("<I", struct.pack("<f", float(HALF_VALUES[h & 0x7FFF])))[0]
    return sign << 31 | bits


def ref_ps2ph(bits, mode, daz):
    sign, exp, mant = bits >> 31, (bits >> 23) & 255, bits & 0x7FFFFF
    if exp == 255:
        return sign << 15 | 0x7C00 | ((0x200 | mant >> 13) if mant else 0)
    if exp == 0:
        if mant == 0 or daz:
            return sign << 15
        x = Fraction(mant, 1 << 149)
    else:
        x = Fraction(0x800000 | mant, 1 << 23) * Fraction(2) ** (exp - 127)
    lo, hi = 0, 0x7BFF
    while lo < hi:                       # largest code whose value is <= x
        mid = (lo + hi + 1) // 2
        if HALF_VALUES[mid] <= x:
            lo = mid
        else:
            hi = mid - 1
    if HALF_VALUES[lo] == x:
        return sign << 15 | lo
    below, above = x - HALF_VALUES[lo], HALF_VALUES[lo + 1] - x
    if mode == 0:
        up = above < below or (above == below and lo & 1)
    elif mode == 3:
        up = False
    else:
        up = (mode == 1) == bool(sign)
    return sign << 15 | (lo + 1 if up else lo)


# -------------------------------------------------------------- harness
k32 = ctypes.WinDLL("kernel32")
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
BUF = k32.VirtualAlloc(None, 0x2000, 0x3000, 0x40)
PRE, STUB, SITE, DATA = BUF, BUF + 0x400, BUF + 0x800, BUF + 0xC00
CTX = ctypes.create_string_buffer(2048)
OTHER = range(2, 16)                     # ymm2..ymm15 must come back unchanged
CTX_ADDR = ctypes.addressof(CTX)
# Registers the stub was told it may use: number -> bytes that must still come back
# unchanged (0: dead, 16: only the upper half is dead). Set by build().
KEEP = {}
RUN = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(PRE)


def ctx(off):
    return M(R.R12, displ=off, displ_size=8)


def encode(instructions, ip):
    enc = ix.BlockEncoder(64)
    enc.add_many(instructions)
    return enc.encode(ip)


def poke(addr, data):
    ctypes.memmove(addr, bytes(data), len(data))


def build(ins_factory, dead=(), upper_dead=()):
    """Place: entry code -> stub -> code after the 'original' instruction."""
    KEEP.clear()
    KEEP.update({r - R.XMM0: 16 for r in upper_dead})
    KEEP.update({r - R.XMM0: 0 for r in dead})
    raw = ix.Encoder(64)
    ins = ins_factory()
    length = raw.encode(ins, SITE)
    original = raw.take_buffer()
    ins = ix.Decoder(64, original, ip=SITE).decode()
    assert ins.len == length and f16c_lower.is_f16c(ins), ins
    stub = f16c_lower.lower(ins, STUB, dead=dead, upper_dead=upper_dead)
    assert len(stub["code"]) <= 0x400
    pre = [I.create_reg(C.PUSH_R64, r) for r in (R.RBX, R.RSI, R.RDI, R.R12)]
    pre += [I.create_reg_reg(C.MOV_R64_RM64, R.R12, R.RCX),
            I.create_reg_i32(C.SUB_RM64_IMM32, R.RSP, 0x108),
            I.create_mem(C.VEX_VSTMXCSR_M32, ctx(140)),
            I.create_mem(C.VEX_VLDMXCSR_M32, ctx(136)),
            I.create_reg_mem(C.VEX_VMOVDQU_YMM_YMMM256, R.YMM2, ctx(144)),
            I.create_mem_reg(C.VEX_VMOVDQU_YMMM256_YMM, M(R.RSP, displ=0x20, displ_size=1), R.YMM2),
            I.create_reg_mem(C.VEX_VMOVDQU_YMM_YMMM256, R.YMM1, ctx(0)),
            I.create_reg_mem(C.VEX_VMOVDQU_YMM_YMMM256, R.YMM0, ctx(32)),
            I.create_mem(C.PUSH_RM64, ctx(128)), I.create(C.POPFQ)]
    pre += [I.create_reg_mem(C.VEX_VMOVDQU_YMM_YMMM256, R.YMM0 + n, ctx(512 + 32 * n)) for n in OTHER if n != 2]
    pre += [I.create_reg_mem(C.MOV_R64_RM64, r, ctx(64 + 8 * n)) for n, r in enumerate(GPRS)]
    pre += [I.create_branch(C.JMP_REL32_64, STUB)]
    post = [I.create(C.PUSHFQ), I.create_mem(C.POP_RM64, ctx(256))]
    post += [I.create_mem_reg(C.MOV_RM64_R64, ctx(264 + 8 * n), r) for n, r in enumerate(GPRS)]
    post += [I.create_mem_reg(C.VEX_VMOVDQU_YMMM256_YMM, ctx(1024 + 32 * n), R.YMM0 + n) for n in OTHER]
    post += [I.create_mem_reg(C.VEX_VMOVDQU_YMMM256_YMM, ctx(328), R.YMM0),
             I.create_mem_reg(C.VEX_VMOVDQU_YMMM256_YMM, ctx(360), R.YMM1),
             I.create_reg_mem(C.VEX_VMOVDQU_YMM_YMMM256, R.YMM2, M(R.RSP, displ=0x20, displ_size=1)),
             I.create_mem_reg(C.VEX_VMOVDQU_YMMM256_YMM, ctx(392), R.YMM2),
             I.create_mem(C.VEX_VLDMXCSR_M32, ctx(140)),
             I.create(C.VEX_VZEROUPPER),
             I.create_reg_i32(C.ADD_RM64_IMM32, R.RSP, 0x108)]
    post += [I.create_reg(C.POP_R64, r) for r in (R.R12, R.RDI, R.RSI, R.RBX)]
    post += [I.create(C.RETNQ)]
    poke(PRE, encode(pre, PRE))
    poke(STUB, stub["code"])
    poke(SITE, original)
    poke(SITE + ins.len, encode(post, SITE + ins.len))
    return ins


FILL = bytes(range(0xA0, 0xC0))
OTHER_FILL = bytes(random.Random(5).getrandbits(8) for _ in range(512))
GPR_IN = [0x1111111111111111 * (n + 1) for n in range(8)]


def run(src=b"", gprs=None, flags=0x202, mxcsr=0x1F80, data=FILL, stack=FILL):
    gprs = gprs or {}
    values = [gprs.get(r, v) for r, v in zip(GPRS, GPR_IN)]
    CTX[0:32] = src.ljust(32, b"\xEE")
    CTX[32:64] = FILL
    CTX[64:128] = struct.pack("<8Q", *values)
    CTX[128:140] = struct.pack("<QI", flags, mxcsr)
    CTX[144:176] = stack
    other = OTHER_FILL
    CTX[512:1024] = other
    poke(DATA, data.ljust(32, b"\xDD"))
    RUN(CTX_ADDR)
    out_flags, = struct.unpack_from("<Q", CTX, 256)
    for n in OTHER:
        want = stack if n == 2 else other[32 * n:32 * n + 32]
        keep = KEEP.get(n, 32)
        assert CTX[1024 + 32 * n:1024 + 32 * n + keep] == want[:keep], f"ymm{n} changed"
    assert list(struct.unpack_from("<8Q", CTX, 264)) == values, "general register changed"
    assert out_flags & FLAG_MASK == flags & FLAG_MASK, f"flags changed {flags:#x} -> {out_flags:#x}"
    return {"ymm0": CTX[328:360], "ymm1": CTX[360:392], "stack": CTX[392:424],
            "data": ctypes.string_at(DATA, 32)}


# ---------------------------------------------------------------- tests
def float_cases(rng):
    mants = [0, 1, 0xFFF, 0x1000, 0x1001, 0x1FFF, 0x2000, 0x2FFF, 0x3000, 0x3001, 0x400000,
             0x7FE000, 0x7FEFFF, 0x7FF000, 0x7FF001, 0x7FFFFF]
    out = []
    for exp in list(range(0, 3)) + list(range(85, 150)) + [200, 254, 255]:
        for mant in mants + [rng.getrandbits(23) for _ in range(6)]:
            out.append(exp << 23 | mant)
    out += [rng.getrandbits(31) for _ in range(4000)]
    out += [rng.randrange(100, 144) << 23 | rng.getrandbits(23) for _ in range(6000)]
    out += [rng.randrange(88, 116) << 23 | rng.getrandbits(10) << 13 | rng.choice([0x1000, 0xFFF, 0x1001, 0])
            for _ in range(4000)]
    return [v | s << 31 for v in out for s in (0, 1)]


def chunks(values, n):
    for i in range(0, len(values), n):
        part = values[i:i + n]
        yield part + [0] * (n - len(part))


def test_ph2ps():
    forms = [
        ("ymm0, xmm1", 8, lambda: I.create_reg_reg(C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM0, R.XMM1), None),
        ("xmm0, xmm1", 4, lambda: I.create_reg_reg(C.VEX_VCVTPH2PS_XMM_XMMM64, R.XMM0, R.XMM1), None),
        ("ymm0, [rax]", 8, lambda: I.create_reg_mem(C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM0, M(R.RAX)), "rax"),
        ("xmm0, [rsi+rbx*2+8]", 4, lambda: I.create_reg_mem(
            C.VEX_VCVTPH2PS_XMM_XMMM64, R.XMM0, M(R.RSI, R.RBX, 2, 8, 1)), "sib"),
        ("ymm0, [rip+data]", 8, lambda: I.create_reg_mem(
            C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM0, M(R.RIP, displ=DATA, displ_size=8)), "rip"),
        ("ymm0, [rsp+0x20]", 8, lambda: I.create_reg_mem(
            C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM0, M(R.RSP, displ=0x20, displ_size=1)), "rsp"),
        ("ymm1, xmm1", 8, lambda: I.create_reg_reg(C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM1, R.XMM1), "alias"),
    ]
    everything = list(range(0x10000))
    for name, count, factory, kind in forms:
        build(factory)
        values = everything if kind in (None, "rax") else everything[::37] + [0x7C01, 0xFE00, 0x0001, 0x83FF]
        for flags in ((0x202, 0xAD7) if kind is None else (0x202,)):
            for part in chunks(values, count):
                raw = struct.pack(f"<{count}H", *part)
                kw = {"src": raw, "flags": flags}
                if kind == "rax":
                    kw.update(gprs={R.RAX: DATA}, data=raw)
                elif kind == "sib":
                    kw.update(gprs={R.RSI: DATA - 8 - 2 * 6, R.RBX: 6}, data=raw)
                elif kind == "rip":
                    kw.update(data=raw)
                elif kind == "rsp":
                    kw.update(stack=raw.ljust(32, b"\x77"))
                out = run(**kw)
                got = out["ymm1" if kind == "alias" else "ymm0"]
                want = struct.pack(f"<{count}I", *map(ref_ph2ps, part)).ljust(32, b"\0")
                assert got == want, f"vcvtph2ps {name}: {part} -> {got.hex()} != {want.hex()}"
                if kind != "alias":
                    assert out["ymm1"][:16] == kw["src"].ljust(16, b"\xEE")[:16], "source changed"
        print(f"  ok  vcvtph2ps {name}")


def test_ps2ph():
    rng = random.Random(16)
    cases = float_cases(rng)
    for imm in (0, 1, 2, 3):
        for count, code, src in ((8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1),
                                 (4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1)):
            build(lambda: I.create_reg_reg_i32(code, R.XMM0, src, imm))
            for n, part in enumerate(chunks(cases, count)):
                # The fixed rounding must not depend on MXCSR.RC / FTZ; DAZ still applies.
                daz = 0x40 if n % 5 == 4 else 0
                mxcsr = 0x1F80 | (n % 4) << 13 | (0x8000 if n % 3 == 1 else 0) | daz
                out = run(src=struct.pack(f"<{count}I", *part), flags=0xAD7 if imm & 1 else 0x202, mxcsr=mxcsr)
                want = struct.pack(f"<{count}H", *(ref_ps2ph(v, imm, bool(daz)) for v in part)).ljust(32, b"\0")
                assert out["ymm0"] == want, f"vcvtps2ph imm={imm} n={count}: {[hex(v) for v in part]} -> " \
                                            f"{out['ymm0'].hex()} != {want.hex()}"
        print(f"  ok  vcvtps2ph xmm0, xmm1/ymm1, {imm}")
    # Rounding taken from MXCSR (imm bit 2), with and without DAZ / FTZ.
    small = cases[::7] + [1, 0x80000001, 0x7FFFFF, 0x807FFFFF]
    for count, code, src in ((8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1),
                             (4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1)):
        build(lambda: I.create_reg_reg_i32(code, R.XMM0, src, 4))
        for mode in range(4):
            for extra in (0, 0x40, 0x8000, 0x8040):
                for part in chunks(small, count):
                    out = run(src=struct.pack(f"<{count}I", *part), mxcsr=0x1F80 | mode << 13 | extra)
                    want = struct.pack(f"<{count}H", *(ref_ps2ph(v, mode, bool(extra & 0x40)) for v in part))
                    assert out["ymm0"] == want.ljust(32, b"\0"), \
                        f"vcvtps2ph MXCSR mode={mode} {extra:#x}: {[hex(v) for v in part]}"
        print(f"  ok  vcvtps2ph xmm0, {'ymm1' if count == 8 else 'xmm1'}, 4 (MXCSR rounding, DAZ, FTZ)")
    # Destination is also the source.
    for count, code, src in ((8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1),
                             (4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1)):
        build(lambda: I.create_reg_reg_i32(code, R.XMM1, src, 0))
        for part in chunks(cases[::13], count):
            out = run(src=struct.pack(f"<{count}I", *part))
            want = struct.pack(f"<{count}H", *(ref_ps2ph(v, 0, False) for v in part)).ljust(32, b"\0")
            assert out["ymm1"] == want, f"vcvtps2ph xmm1, {src}: {[hex(v) for v in part]}"
        print(f"  ok  vcvtps2ph xmm1, {'ymm1' if count == 8 else 'xmm1'}, 0")
    # Memory destinations: exactly 8 or 16 bytes written, nothing else touched.
    forms = [
        ("[rax], ymm1", 8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1, lambda: M(R.RAX), "rax"),
        ("[rdi+r9*4-4], xmm1", 4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1,
         lambda: M(R.RDI, R.R9, 4, -4, 1), "sib"),
        ("[rip+data], ymm1", 8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1,
         lambda: M(R.RIP, displ=DATA, displ_size=8), "rip"),
        ("[rsp+0x20], ymm1", 8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1,
         lambda: M(R.RSP, displ=0x20, displ_size=1), "rsp"),
        ("[rsp+0x28], xmm1", 4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1,
         lambda: M(R.RSP, displ=0x28, displ_size=1), "rsp8"),
    ]
    for name, count, code, src, mem, kind in forms:
        build(lambda: I.create_mem_reg_i32(code, mem(), src, 0))
        for part in chunks(cases[::11], count):
            gprs = {"rax": {R.RAX: DATA}, "sib": {R.RDI: DATA + 4 - 12, R.R9: 3}}.get(kind, {})
            out = run(src=struct.pack(f"<{count}I", *part), gprs=gprs)
            halves = struct.pack(f"<{count}H", *(ref_ps2ph(v, 0, False) for v in part))
            if kind == "rsp":
                assert out["stack"] == halves + FILL[16:], name
            elif kind == "rsp8":
                assert out["stack"] == FILL[:8] + halves + FILL[16:], name
            else:
                assert out["data"] == halves + FILL[len(halves):], f"vcvtps2ph {name}: {out['data'].hex()}"
            assert out["ymm0"] == FILL, "register changed by a memory-destination form"
        print(f"  ok  vcvtps2ph {name}, 0")


def test_free_registers():
    """Stubs that use dead / upper-dead registers: same results, and only those registers change."""
    rng = random.Random(23)
    cases = float_cases(rng)[::5]
    halves = list(range(0, 0x10000, 7)) + [0x7C01, 0xFE00, 0x0001, 0x83FF, 0x7BFF]
    layouts = [
        ("todos livres", {R.XMM0 + n for n in range(3, 16)}, ()),
        ("so metade superior livre", (), {R.XMM0 + n for n in range(3, 16)}),
        ("misturado", {R.XMM4, R.XMM9}, {R.XMM3, R.XMM5, R.XMM12}),
        ("um livre (ymm2, usado na pilha)", {R.XMM3}, ()),
    ]
    for label, dead, upper in layouts:
        for count, code, src in ((8, C.VEX_VCVTPH2PS_YMM_XMMM128, R.XMM1), (4, C.VEX_VCVTPH2PS_XMM_XMMM64, R.XMM1)):
            build(lambda: I.create_reg_reg(code, R.YMM0 if count == 8 else R.XMM0, src), dead, upper)
            for part in chunks(halves, count):
                raw = struct.pack(f"<{count}H", *part)
                out = run(src=raw, flags=0xAD7)
                want = struct.pack(f"<{count}I", *map(ref_ph2ps, part)).ljust(32, b"\0")
                assert out["ymm0"] == want, f"vcvtph2ps ({label}): {part}"
        for imm in (0, 1, 2, 3, 4):
            for count, code, src in ((8, C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.YMM1),
                                     (4, C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, R.XMM1)):
                build(lambda: I.create_reg_reg_i32(code, R.XMM0, src, imm), dead, upper)
                for n, part in enumerate(chunks(cases, count)):
                    daz = 0x40 if n % 3 == 2 else 0
                    mode = n % 4 if imm == 4 else imm
                    out = run(src=struct.pack(f"<{count}I", *part), mxcsr=0x1F80 | (n % 4) << 13 | daz)
                    want = struct.pack(f"<{count}H", *(ref_ps2ph(v, mode, bool(daz)) for v in part)).ljust(32, b"\0")
                    assert out["ymm0"] == want, f"vcvtps2ph imm={imm} ({label}): {[hex(v) for v in part]}"
            build(lambda: I.create_mem_reg_i32(C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, M(R.RSP, displ=0x20, displ_size=1),
                                               R.YMM1, imm), dead, upper)
            for n, part in enumerate(chunks(cases[::3], 8)):
                mode = n % 4 if imm == 4 else imm
                out = run(src=struct.pack("<8I", *part), mxcsr=0x1F80 | (n % 4) << 13)
                want = struct.pack("<8H", *(ref_ps2ph(v, mode, False) for v in part))
                assert out["stack"] == want + FILL[16:], f"vcvtps2ph [rsp+0x20] imm={imm} ({label})"
        print(f"  ok  {label}")
    KEEP.clear()


def test_liveness():
    """What the analysis may and may not call dead."""
    def regs(*names):
        return frozenset(getattr(R, n.upper()) for n in names)

    def live_after(*instructions):
        code = encode(list(instructions) + [I.create(C.RETNQ)], 0x1000)
        return f16c_lower.vector_liveness(code, 0x1000)

    X = lambda n: R.XMM0 + n
    Y = lambda n: R.YMM0 + n
    cases = [
        # VEX write before any read: whole register dead
        ([I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(3), X(3), X(3))], regs("xmm3"), regs("xmm3")),
        ([I.create_reg_reg(C.VEX_VMOVAPS_YMM_YMMM256, Y(4), Y(5))], regs("xmm4"), regs("xmm4")),
        # read first: live
        ([I.create_reg_reg_reg(C.VEX_VADDPS_XMM_XMM_XMMM128, X(6), X(6), X(7))], regs(), regs("xmm6")),
        ([I.create_reg_reg(C.VEX_VMOVAPS_YMM_YMMM256, Y(4), Y(5)),
          I.create_reg_reg(C.VEX_VMOVAPS_YMM_YMMM256, Y(5), Y(4))], regs("xmm4"), regs("xmm4")),
        # legacy SSE write keeps the upper half: not dead
        ([I.create_reg_reg(C.MOVAPS_XMM_XMMM128, X(8), X(9))], regs(), regs()),
        # only the low half read, then a VEX write: upper half dead
        ([I.create_mem_reg(C.VEX_VMOVUPS_XMMM128_XMM, M(R.RAX), X(10)),
          I.create_reg_mem(C.VEX_VMOVUPS_XMM_XMMM128, X(10), M(R.RAX))], regs(), regs("xmm10")),
        # 256-bit read: everything live
        ([I.create_mem_reg(C.VEX_VMOVUPS_YMMM256_YMM, M(R.RAX), Y(11)),
          I.create_reg_mem(C.VEX_VMOVUPS_XMM_XMMM128, X(11), M(R.RAX))], regs(), regs()),
        # a jump out of the examined code, or a call: nothing after it counts
        ([I.create_branch(C.JMP_REL32_64, 0x2000),
          I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(3), X(3), X(3))], regs(), regs()),
        ([I.create_branch(C.CALL_REL32_64, 0x2000),
          I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(3), X(3), X(3))], regs(), regs()),
        # vzeroupper clears every upper half and keeps the low halves
        ([I.create(C.VEX_VZEROUPPER), I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(3), X(3), X(3))],
         regs("xmm3"), regs(*[f"xmm{n}" for n in range(16)])),
        # blend reads its mask register (low half only: the upper half is still dead)
        ([I.create_reg_reg_reg_reg(C.VEX_VBLENDVPS_XMM_XMM_XMMM128_XMM, X(1), X(2), X(3), X(4)),
          I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(4), X(4), X(4))], regs("xmm1"), regs("xmm1", "xmm4")),
    ]
    # Branches: both sides of a conditional jump, jumps over code, loops.
    lbl = lambda n: 0x1000 + 0x40 * n
    xor = lambda n: I.create_reg_reg_reg(C.VEX_VXORPS_XMM_XMM_XMMM128, X(n), X(n), X(n))
    add = lambda d, a, b: I.create_reg_reg_reg(C.VEX_VADDPS_XMM_XMM_XMMM128, X(d), X(a), X(b))

    def flow(blocks):
        """blocks[k] placed at 0x1000 + 0x40*k; returns liveness at block 0."""
        image = bytearray(b"\xcc" * 0x40 * len(blocks))
        for k, block in enumerate(blocks):
            code = encode(block, lbl(k))
            assert len(code) <= 0x40
            image[0x40 * k:0x40 * k + len(code)] = code
        return f16c_lower.vector_liveness(bytes(image), 0x1000)
    ret = I.create(C.RETNQ)
    branchy = [
        # if/else: xmm3, xmm5 written on every path -> dead; xmm4 read on one side -> live
        ([[I.create_branch(C.JE_REL32_64, lbl(1)), xor(3), xor(4), I.create_branch(C.JMP_REL32_64, lbl(2))],
          [xor(3), add(5, 4, 4), I.create_branch(C.JMP_REL32_64, lbl(2))],
          [xor(5), ret]], regs("xmm3", "xmm5"), regs()),
        # loop that reads xmm6 every turn and writes xmm7 before reading it
        ([[xor(7), add(8, 6, 7), I.create_branch(C.JNE_REL32_64, lbl(0))],
          ], regs("xmm7", "xmm8"), regs()),
        # loop exit reaches a return before xmm9 is written: live
        ([[I.create_branch(C.JNE_REL32_64, lbl(1)), xor(9), ret], [ret]], regs(), regs()),
    ]
    for n, (blocks, dead, upper) in enumerate(branchy):
        got = flow(blocks)
        assert got == (dead, upper | dead), f"branch case {n}: {got} != {(dead, upper | dead)}"
    for n, (code, dead, upper) in enumerate(cases):
        got = live_after(*code)
        assert got == (dead, upper | dead), f"case {n}: {got} != {(dead, upper | dead)}"
    print(f"  ok  {len(cases) + len(branchy)} sequencias")


if __name__ == "__main__":
    print("analise de registradores livres")
    test_liveness()
    print("vcvtph2ps: every one of the 65536 half values")
    test_ph2ps()
    print("vcvtps2ph: boundary and random floats, all rounding modes")
    test_ps2ph()
    print("stubs using dead / upper-dead registers")
    test_free_registers()
    print("ALL TESTS PASSED")
