"""Speed of the replacement code: nanoseconds per converted instruction.

    python\\python.exe tests\\bench_lower.py [other_f16c_lower.py ...]

Each extra argument is another version of f16c_lower.py to compare against.
"""
import ctypes
import importlib.util
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "core" / "pylibs"), str(ROOT / "core")]
import iced_x86 as ix
import f16c_lower

R, C, I, M = ix.Register, ix.Code, ix.Instruction, ix.MemoryOperand
k32 = ctypes.WinDLL("kernel32")
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
BUF = k32.VirtualAlloc(None, 0x4000, 0x3000, 0x40)
ENTRY, SITE, STUB, DATA, SRC = BUF, BUF + 0x100, BUF + 0x400, BUF + 0x3000, BUF + 0x3800
RUN = ctypes.CFUNCTYPE(None, ctypes.c_uint64)(ENTRY)
ITER = 2_000_000
VALUES = (1.5, -2.25, 65504.0, 1e-6, 3.14159, -0.0, 1e-30, 7.0)

FORMS = [
    ("vcvtph2ps ymm0, xmm1", lambda: I.create_reg_reg(C.VEX_VCVTPH2PS_YMM_XMMM128, R.YMM0, R.XMM1)),
    ("vcvtph2ps xmm0, [rax]", lambda: I.create_reg_mem(C.VEX_VCVTPH2PS_XMM_XMMM64, R.XMM0, M(R.RAX))),
    ("vcvtps2ph xmm0, ymm1, 0", lambda: I.create_reg_reg_i32(C.VEX_VCVTPS2PH_XMMM128_YMM_IMM8, R.XMM0, R.YMM1, 0)),
    ("vcvtps2ph [rax], xmm1, 4", lambda: I.create_mem_reg_i32(C.VEX_VCVTPS2PH_XMMM64_XMM_IMM8, M(R.RAX), R.XMM1, 4)),
]


def encode(instructions, ip):
    enc = ix.BlockEncoder(64)
    enc.add_many(instructions)
    return enc.encode(ip)


def poke(addr, data):
    ctypes.memmove(addr, bytes(data), len(data))


def measure(module, factory, dead=None):
    raw = ix.Encoder(64)
    raw.encode(factory(), SITE)
    ins = ix.Decoder(64, raw.take_buffer(), ip=SITE).decode()
    others = {R.XMM0 + n for n in range(2, 16)}
    if dead and hasattr(module, "vector_liveness"):
        # The loop below only uses xmm0/xmm1: the other registers are free, as the
        # patcher finds in code that overwrites its scratch registers right after
        # ("livres": all of them; "metade livre": only their upper 128 bits).
        stub = module.lower(ins, STUB, **{dead: others})
    else:
        stub = module.lower(ins, STUB)
    poke(STUB, stub["code"])
    poke(SITE, b"\xe9" + struct.pack("<i", STUB - (SITE + 5)) + b"\x90" * (ins.len - 5))
    # rcx = count; xmm1/ymm1 = mixed values; rax = data buffer (halves)
    poke(SRC, struct.pack("<8f", *VALUES))
    poke(DATA, struct.pack("<8H", 0x3C00, 0xC000, 0x7BFF, 0x0011, 0x4248, 0x8000, 0x0001, 0x4700))
    poke(ENTRY, encode([
        # Inside a real function RSP is 16-byte aligned; on entry here it is not.
        I.create_reg_i32(C.SUB_RM64_IMM8, R.RSP, 8),
        I.create_reg_i64(C.MOV_R64_IMM64, R.RAX, SRC),
        I.create_reg_mem(C.VEX_VMOVUPS_YMM_YMMM256, R.YMM1, M(R.RAX)),
        I.create_reg_i64(C.MOV_R64_IMM64, R.RAX, DATA),
        I.create_branch(C.JMP_REL32_64, SITE)], ENTRY))
    poke(SITE + ins.len, encode([
        I.create_reg(C.DEC_RM64, R.RCX),
        I.create_branch(C.JNE_REL32_64, SITE),
        I.create(C.VEX_VZEROUPPER),
        I.create_reg_i32(C.ADD_RM64_IMM8, R.RSP, 8),
        I.create(C.RETNQ)], SITE + ins.len))
    RUN(1000)
    best = min(_timed(ITER) for _ in range(9))
    return best / ITER * 1e9


def _timed(n):
    t = time.perf_counter()
    RUN(n)
    return time.perf_counter() - t


def main():
    versions = [("atual", f16c_lower)]
    for path in sys.argv[1:]:
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        versions.append((Path(path).stem, mod))
    print(f"{'instrucao':28}" + "".join(f"{name:>22}" for name, _ in versions))
    global VALUES
    common, denormal = VALUES, (1e-40, -1e-41, 1e-39, 2e-45) * 2
    for label, values, dead in (("valores comuns, registradores ocupados", common, None),
                                ("valores comuns, so a metade superior livre depois", common, "upper_dead"),
                                ("valores comuns, registradores livres depois", common, "dead"),
                                ("floats denormais, registradores ocupados", denormal, None)):
        VALUES = values
        print(f"-- {label}")
        for title, factory in FORMS:
            print(f"{title:28}" + "".join(f"{measure(mod, factory, dead):19.1f} ns" for _, mod in versions))


if __name__ == "__main__":
    main()
