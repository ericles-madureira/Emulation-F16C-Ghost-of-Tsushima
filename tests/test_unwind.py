"""Checks the stack-unwind data of a patched file with Windows' own unwinder.

For every converted instruction, unwinding from inside its replacement code
must land exactly where unwinding from the original instruction would have.

    python\\python.exe tests\\test_unwind.py <patched exe> [more...]
"""
import ctypes
import json
import struct
import sys
from pathlib import Path

k32 = ctypes.WinDLL("kernel32")
ntdll = ctypes.WinDLL("ntdll")
k32.LoadLibraryExW.restype = ctypes.c_void_p
k32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
ntdll.RtlLookupFunctionEntry.restype = ctypes.c_void_p
ntdll.RtlLookupFunctionEntry.argtypes = [ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
ntdll.RtlVirtualUnwind.restype = ctypes.c_void_p
ntdll.RtlVirtualUnwind.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p]
RSP, RIP, CONTEXT_SIZE = 0x98, 0xF8, 1232
STACK = (ctypes.c_uint64 * 0x4000)(*range(0x7000000000, 0x7000004000))
STACK_TOP = ctypes.addressof(STACK) + 0x8000


def unwind(base, rip, rsp):
    raw = ctypes.create_string_buffer(CONTEXT_SIZE + 16)
    ctx = (ctypes.addressof(raw) + 15) & ~15
    ctypes.memset(ctx, 0, CONTEXT_SIZE)
    # A frame-pointer function recomputes RSP from its frame register: give
    # every register the same stack address so both unwinds stay comparable.
    for off in range(0x78, 0xF8, 8):
        ctypes.c_uint64.from_address(ctx + off).value = STACK_TOP
    ctypes.c_uint64.from_address(ctx + RSP).value = rsp
    ctypes.c_uint64.from_address(ctx + RIP).value = rip
    image = ctypes.c_uint64()
    entry = ntdll.RtlLookupFunctionEntry(rip, ctypes.byref(image), None)
    if not entry:                                    # leaf: return address is at [rsp]
        return ctypes.c_uint64.from_address(rsp).value, rsp + 8, False
    handler, frame = ctypes.c_void_p(), ctypes.c_uint64()
    ntdll.RtlVirtualUnwind(0, image.value, rip, entry, ctx, ctypes.byref(handler), ctypes.byref(frame), None)
    return (ctypes.c_uint64.from_address(ctx + RIP).value, ctypes.c_uint64.from_address(ctx + RSP).value, True)


def check(path: Path):
    report = json.loads(Path(str(path) + ".f16c-report.json").read_text(encoding="utf-8"))
    base = k32.LoadLibraryExW(str(path), None, 1)    # DONT_RESOLVE_DLL_REFERENCES: nothing runs
    if not base:
        raise SystemExit(f"could not map {path}")
    bad = with_entry = 0
    for rec in report["records"]:
        site, stub, size = int(rec["rva"], 16), int(rec["stub_rva"], 16), rec["stub_size"]
        tail = rec["stub_tail"]
        frame, prolog = rec.get("stub_frame", 128), rec.get("stub_prolog", 5)
        # Reference: the instruction right after the converted one.
        want = unwind(base, base + site + len(rec["original"]) // 2, STACK_TOP)
        with_entry += want[2]
        probes = [(0, 0), (prolog, frame), (size // 2, frame), (tail - 8, frame), (tail, 0), (size - 5, 0)]
        for offset, depth in probes:
            got = unwind(base, base + stub + offset, STACK_TOP - depth)
            if got[:2] != want[:2]:
                bad += 1
                if bad <= 10:
                    print(f"  MISMATCH site {site:#x} stub+{offset}: {got[0]:#x}/{got[1]:#x} "
                          f"expected {want[0]:#x}/{want[1]:#x}")
    total = len(report["records"])
    print(f"{path.name}: {total} sites ({with_entry} inside functions with unwind data), "
          f"{total * 6} probes, {bad} mismatches -> {'FAIL' if bad else 'PASS'}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if sum(check(Path(p).resolve()) for p in sys.argv[1:]) else 0)
