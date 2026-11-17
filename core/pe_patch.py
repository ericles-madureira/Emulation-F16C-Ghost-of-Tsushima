"""Rewrites the F16C instructions of a 64-bit Windows exe/dll.

Each instruction is overwritten with a JMP to replacement code placed in a new
section (.f16c). Nothing else in the file is moved. No extra DLL is needed.
The input file is only read; the caller decides where the result is written.
"""
from __future__ import annotations

import bisect
import ctypes
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pylibs"))
import iced_x86 as ix
import pefile

import f16c_lower

SECTION = b".f16c\0\0\0"
MARKER = b"F16C-EMU patched"      # first 16 bytes of the added code area


class PatchError(Exception):
    pass


def align(n, a):
    return (n + a - 1) & ~(a - 1)


def load(path: Path):
    data = Path(path).read_bytes()
    try:
        pe = pefile.PE(data=data, fast_load=True)
    except pefile.PEFormatError as err:
        raise PatchError(f"not a Windows executable: {err}") from None
    if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Magic != 0x20B:
        raise PatchError("only 64-bit (x64) executables are supported")
    return pe, data


def is_patched(pe) -> bool:
    if any(s.Name == SECTION for s in pe.sections):
        return True
    # No room for a new section header: the code was appended to the last section instead.
    last = max(pe.sections, key=lambda s: s.VirtualAddress)
    return bool(last.Characteristics & 0x20000000) and MARKER in last.get_data()


def _header_room(pe, data) -> bool:
    opt = pe.OPTIONAL_HEADER
    header = pe.sections[-1].get_file_offset() + 40
    first_raw = min((s.PointerToRawData for s in pe.sections if s.SizeOfRawData and s.PointerToRawData),
                    default=opt.SizeOfHeaders)
    return header + 40 <= min(opt.SizeOfHeaders, first_raw) and not any(data[header:header + 40])


def sweep(pe, progress=None) -> list[int]:
    """RVAs of every F16C instruction a straight decode of the code sections finds."""
    base = pe.OPTIONAL_HEADER.ImageBase
    found = []
    for sec in pe.sections:
        if not sec.Characteristics & 0x20000000:
            continue
        raw = sec.get_data()[:sec.Misc_VirtualSize]
        # Both instructions are VEX-encoded: C4 .. .. 13 (ph2ps) or C4 .. .. 1D (ps2ph).
        if b"\xc4" not in raw:
            continue
        for n, ins in enumerate(ix.Decoder(64, raw, ip=base + sec.VirtualAddress)):
            if ins.encoding == ix.EncodingKind.VEX and f16c_lower.is_f16c(ins):
                found.append(ins.ip - base)
            if progress and n % 4_000_000 == 0:
                progress(sec.Name.rstrip(b"\0").decode(errors="replace"), ins.ip - base - sec.VirtualAddress,
                         len(raw))
    return found


def cpuid_sites(pe) -> list[tuple[int, int]]:
    """CPUID instructions that can be redirected: (rva, bytes to move).

    CPUID is 2 bytes, a jump needs 5, so the instructions right after it move
    to the new code too. A site is only used when that is clearly safe: it is
    inside a known function, what follows is plain straight-line code, and no
    branch anywhere lands in the middle of the moved bytes."""
    base = pe.OPTIONAL_HEADER.ImageBase
    pdata_dir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
    table = pe.get_data(pdata_dir.VirtualAddress, pdata_dir.Size) if pdata_dir.Size else b""
    functions = sorted(f[:2] for f in struct.iter_unpack("<III", table[:len(table) // 12 * 12]))
    starts = [f[0] for f in functions]

    def in_function(rva, end):
        i = bisect.bisect_right(starts, rva) - 1
        return i >= 0 and end <= functions[i][1]

    code = [(sec.VirtualAddress, sec.get_data()[:sec.Misc_VirtualSize]) for sec in pe.sections
            if sec.Characteristics & 0x20000000]
    candidates = {}
    for start, raw in code:
        if b"\x0f\xa2" not in raw:
            continue
        current = None                                   # [rva, length] being collected
        for ins in ix.Decoder(64, raw, ip=base + start):
            if current is not None:
                plain = (not ins.is_invalid and ins.flow_control == ix.FlowControl.NEXT
                         and ins.code != ix.Code.CPUID and not f16c_lower.is_f16c(ins)
                         and ins.memory_base != ix.Register.EIP)
                if not plain:
                    current = None
                else:
                    current[1] += ins.len
                    if current[1] >= 5:
                        if in_function(current[0], current[0] + current[1]):
                            candidates[current[0]] = current[1]
                        current = None
                    continue
            if ins.code == ix.Code.CPUID:
                current = [ins.ip - base, ins.len]
    if not candidates:
        return []
    inside = {rva + n: rva for rva, length in candidates.items() for n in range(1, length)}
    near = (ix.OpKind.NEAR_BRANCH16, ix.OpKind.NEAR_BRANCH32, ix.OpKind.NEAR_BRANCH64)
    for start, raw in code:
        for ins in ix.Decoder(64, raw, ip=base + start):
            if ins.op0_kind in near and ins.near_branch_target - base in inside:
                candidates.pop(inside[ins.near_branch_target - base], None)
    return sorted(candidates.items())


def _code_reader(pe):
    """fetch(address) -> up to 15 bytes of the program's code there (b"" outside it)."""
    base = pe.OPTIONAL_HEADER.ImageBase
    sections = sorted((base + sec.VirtualAddress, sec.get_data()[:sec.Misc_VirtualSize]) for sec in pe.sections
                      if sec.Characteristics & 0x20000000)
    starts = [start for start, _ in sections]

    def fetch(address):
        i = bisect.bisect_right(starts, address) - 1
        if i < 0:
            return b""
        start, raw = sections[i]
        return raw[address - start:address - start + 15]
    return fetch


def _cpuid_stub(insns, ip, back):
    """CPUID that also reports F16C (leaf 1, ECX bit 29), then the moved instructions."""
    R, C, I, M = ix.Register, ix.Code, ix.Instruction, ix.MemoryOperand
    a = f16c_lower._Asm()
    a.emit(I.create(C.PUSHFQ))                           # CPUID leaves the flags alone; so do we
    a.emit(I.create_reg(C.PUSH_R64, R.RAX))              # the leaf that was asked for
    a.emit(I.create(C.CPUID))
    a.emit(I.create_mem_i32(C.CMP_RM32_IMM8, M(R.RSP), 1))
    a.jump(C.JNE_REL32_64, "done")
    a.emit(I.create_reg_i32(C.OR_RM32_IMM32, R.ECX, 1 << 29))
    a.label("done")
    a.emit(I.create_reg_mem(C.LEA_R64_M, R.RSP, M(R.RSP, displ=8, displ_size=1)))
    a.emit(I.create(C.POPFQ))
    for ins in insns:
        a.emit(ins)
    a.emit(I.create_branch(C.JMP_REL32_64, back))
    code = a.encode(ip)
    decoded = list(ix.Decoder(64, code, ip=ip))
    if any(d.is_invalid for d in decoded) or decoded[-1].near_branch_target != back:
        raise f16c_lower.LoweringError("CPUID replacement does not decode cleanly")
    tail = next(d.next_ip - ip for d in decoded if d.code == C.POPFQ)
    return code, tail


def _checksum(data: bytearray) -> int:
    fn = ctypes.WinDLL("imagehlp").CheckSumMappedFile
    fn.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                   ctypes.POINTER(ctypes.c_uint32)]
    fn.restype = ctypes.c_void_p
    old, new = ctypes.c_uint32(), ctypes.c_uint32()
    buf = (ctypes.c_ubyte * len(data)).from_buffer(data)
    ok = fn(buf, len(data), ctypes.byref(old), ctypes.byref(new))
    del buf
    if not ok:
        raise PatchError("Windows could not compute the PE checksum")
    return new.value


def patch(pe, original: bytes, sites: list[int], cpuid=()) -> tuple[bytearray, dict]:
    """Returns (patched file bytes, report).

    sites: RVAs of F16C instructions. cpuid: result of cpuid_sites(), to make
    the program's own "does this CPU have F16C?" question answer yes."""
    if is_patched(pe):
        raise PatchError("this file was already patched")
    opt = pe.OPTIONAL_HEADER
    base, sec_align, file_align = opt.ImageBase, opt.SectionAlignment, opt.FileAlignment
    data = bytearray(original)
    fmt = ix.Formatter(ix.FormatterSyntax.INTEL)

    # A digital signature stops being valid; drop it (it sits at the end of the file).
    cert = opt.DATA_DIRECTORY[4]
    signature_removed = bool(cert.VirtualAddress)
    if cert.VirtualAddress and cert.VirtualAddress + cert.Size >= len(data) - 8:
        del data[cert.VirtualAddress:]
    struct.pack_into("<II", data, cert.get_field_absolute_offset("VirtualAddress"), 0, 0)

    # Where the new code goes: a section of its own when the header table has a
    # free slot, otherwise the end of the last section, which is then enlarged.
    last = max(pe.sections, key=lambda s: s.VirtualAddress)
    own_section = _header_room(pe, data)
    if own_section:
        new_rva = align(max([opt.SizeOfImage] + [s.VirtualAddress + s.Misc_VirtualSize for s in pe.sections]),
                        sec_align)
    else:
        raw_end = last.PointerToRawData + last.SizeOfRawData
        if (last is not pe.sections[-1] or not last.SizeOfRawData or len(data) > align(raw_end, file_align)
                or any(s.PointerToRawData + s.SizeOfRawData > raw_end for s in pe.sections)):
            raise PatchError("the file header has no room for a new section and the last section "
                             "cannot be enlarged (data follows it in the file)")
        del data[raw_end:]
        new_rva = last.VirtualAddress + align(max(last.Misc_VirtualSize, last.SizeOfRawData), 16)
    payload = bytearray(MARKER)

    def append(blob, alignment):
        pos = align(len(payload), alignment)
        payload.extend(b"\xcc" * (pos - len(payload)))
        payload.extend(blob)
        return new_rva + pos

    # Constants shared by every replacement (read-only data, kept in the code section).
    pool = base + append(f16c_lower.POOL, f16c_lower.POOL_ALIGN)

    # Existing function table: lets the new code inherit its function's stack description.
    pdata_dir = opt.DATA_DIRECTORY[3]
    old_pdata = pe.get_data(pdata_dir.VirtualAddress, pdata_dir.Size) if pdata_dir.Size else b""
    old_pdata = old_pdata[:len(old_pdata) // 12 * 12]
    functions = list(struct.iter_unpack("<III", old_pdata))
    starts = [f[0] for f in functions]
    if starts != sorted(starts):         # malformed table: do not rely on it
        functions, starts = [], []

    def parent_of(rva):
        """Index of the function-table entry that covers rva, or None."""
        i = bisect.bisect_right(starts, rva) - 1
        return i if i >= 0 and rva < functions[i][1] else None

    records, skipped, new_functions = [], [], []
    fetch, decoded = _code_reader(pe), {}
    for rva in sorted(set(sites)):
        raw = pe.get_data(rva, 15)
        ins = ix.Decoder(64, raw, ip=base + rva).decode()
        if ins.is_invalid or not f16c_lower.is_f16c(ins):
            skipped.append({"rva": hex(rva), "reason": "not an F16C instruction"})
            continue
        if ins.len < 5:
            skipped.append({"rva": hex(rva), "asm": fmt.format(ins), "reason": "shorter than a jump"})
            continue
        offset = pe.get_offset_from_rva(rva)
        if bytes(data[offset:offset + ins.len]) != raw[:ins.len]:
            skipped.append({"rva": hex(rva), "asm": fmt.format(ins), "reason": "not stored in the file"})
            continue
        stub_rva = new_rva + align(len(payload), 16)
        # Registers the program overwrites right after the instruction need no saving.
        dead, upper_dead = f16c_lower.vector_liveness(fetch, ins.next_ip, cache=decoded)
        try:
            stub = f16c_lower.lower(ins, base + stub_rva, pool, dead, upper_dead)
        except f16c_lower.LoweringError as err:
            skipped.append({"rva": hex(rva), "asm": fmt.format(ins), "reason": str(err)})
            continue
        code = stub["code"]
        assert append(code, 16) == stub_rva
        data[offset:offset + ins.len] = b"\xe9" + struct.pack("<i", stub_rva - (rva + 5)) + b"\x90" * (ins.len - 5)

        # Unwind data: "<frame> bytes of stack on top of the surrounding function's frame".
        parent = parent_of(rva)
        frame, prolog = stub["frame"], stub["prolog"]
        records.append({"rva": hex(rva), "asm": fmt.format(ins), "original": raw[:ins.len].hex(),
                        "stub_rva": hex(stub_rva), "stub_size": len(code), "stub_tail": stub["tail"],
                        "stub_frame": frame, "stub_prolog": prolog, "saved_regs": stub["saved"]})
        if not frame:                    # no stack used: the function's own description applies
            if parent is not None:
                new_functions.append((stub_rva, stub_rva + len(code), None, parent))
            continue
        chain = struct.pack("<III", *functions[parent]) if parent is not None else b""
        flags = 0x20 if parent is not None else 0          # UNW_FLAG_CHAININFO
        if frame <= 128:                                   # UWOP_ALLOC_SMALL (+ padding slot)
            codes = bytes([prolog, (frame // 8 - 1) << 4 | 2, 0, 0])
            count = 1
        else:                                              # UWOP_ALLOC_LARGE, size / 8 in the next slot
            codes = bytes([prolog, 0x01]) + struct.pack("<H", frame // 8)
            count = 2
        body = bytes([1 | flags, prolog, count, 0]) + codes + chain
        new_functions.append((stub_rva, stub_rva + stub["tail"], append(body, 4), None))
        if parent is not None:           # stack already released: same state as the function itself
            new_functions.append((stub_rva + stub["tail"], stub_rva + len(code), None, parent))

    if not records:
        return data, {"patched": 0, "skipped": skipped, "records": []}

    cpuid_records = []
    for rva, length in cpuid:
        raw = pe.get_data(rva, length)
        insns = list(ix.Decoder(64, raw, ip=base + rva))
        offset = pe.get_offset_from_rva(rva)
        if (insns[0].code != ix.Code.CPUID or sum(i.len for i in insns) != length or length < 5
                or bytes(data[offset:offset + length]) != raw):
            continue                                     # overlaps something already changed
        stub_rva = new_rva + align(len(payload), 16)
        try:
            code, tail = _cpuid_stub(insns[1:], base + stub_rva, base + rva + length)
        except Exception as err:                         # an instruction that cannot be moved
            skipped.append({"rva": hex(rva), "asm": "cpuid", "reason": str(err)})
            continue
        assert append(code, 16) == stub_rva
        data[offset:offset + length] = b"\xe9" + struct.pack("<i", stub_rva - (rva + 5)) + b"\x90" * (length - 5)
        parent = parent_of(rva)
        if parent is not None:           # the moved instructions run in the function's own frame
            new_functions.append((stub_rva + tail, stub_rva + len(code), None, parent))
        cpuid_records.append({"rva": hex(rva), "moved": raw.hex(), "stub_rva": hex(stub_rva)})

    if new_functions:
        table_rva = new_rva + align(len(payload), 4)
        # An odd "unwind" value means: use the table entry at (value - 1) instead.
        table = old_pdata + b"".join(
            struct.pack("<III", begin, end, xdata if indirect is None else (table_rva + 12 * indirect) | 1)
            for begin, end, xdata, indirect in new_functions)
        assert append(table, 4) == table_rva
        struct.pack_into("<II", data, pdata_dir.get_field_absolute_offset("VirtualAddress"), table_rva, len(table))

    if own_section:
        header = pe.sections[-1].get_file_offset() + 40
        raw_ptr = align(len(data), file_align)
        raw_size = align(len(payload), file_align)
        data.extend(b"\0" * (raw_ptr - len(data)))
        data.extend(payload)
        data.extend(b"\0" * (raw_size - len(payload)))
        data[header:header + 40] = struct.pack("<8sIIIIIIHHI", SECTION, len(payload), new_rva, raw_size, raw_ptr,
                                               0, 0, 0, 0, 0x60000020)
        struct.pack_into("<H", data, pe.FILE_HEADER.get_field_absolute_offset("NumberOfSections"),
                         pe.FILE_HEADER.NumberOfSections + 1)
        where = SECTION.rstrip(b"\0").decode()
    else:
        # File position mirrors the address: offset in section == offset in memory.
        start = last.PointerToRawData + (new_rva - last.VirtualAddress)
        data.extend(b"\0" * (start - len(data)))
        data.extend(payload)
        data.extend(b"\0" * (align(len(data), file_align) - len(data)))
        raw_size = len(data) - last.PointerToRawData - last.SizeOfRawData
        struct.pack_into("<I", data, last.get_field_absolute_offset("Misc_VirtualSize"),
                         new_rva - last.VirtualAddress + len(payload))
        struct.pack_into("<I", data, last.get_field_absolute_offset("SizeOfRawData"),
                         len(data) - last.PointerToRawData)
        # executable + readable code, and no longer "discardable"
        struct.pack_into("<I", data, last.get_field_absolute_offset("Characteristics"),
                         (last.Characteristics | 0x60000020) & ~0x02000000)
        where = last.Name.rstrip(b"\0").decode(errors="replace") + " (enlarged)"
    struct.pack_into("<I", data, opt.get_field_absolute_offset("SizeOfImage"),
                     align(new_rva + len(payload), sec_align))
    struct.pack_into("<I", data, opt.get_field_absolute_offset("SizeOfCode"), opt.SizeOfCode + raw_size)
    checksum_at = opt.get_field_absolute_offset("CheckSum")
    struct.pack_into("<I", data, checksum_at, 0)
    struct.pack_into("<I", data, checksum_at, _checksum(data))

    return data, {"patched": len(records), "skipped": skipped, "placed_in": where, "section_rva": hex(new_rva),
                  "section_size": len(payload), "signature_removed": signature_removed, "cpuid_patched": cpuid_records, "records": records}
