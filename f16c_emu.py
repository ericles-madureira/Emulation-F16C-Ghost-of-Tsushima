"""FX-Reborn-F16C-Ultra 3.0: faz um executavel que exige F16C rodar em processadores sem F16C.

Reescreve, dentro do proprio .exe/.dll, as duas instrucoes do conjunto F16C
(VCVTPH2PS e VCVTPS2PH) por codigo equivalente que qualquer processador com
AVX executa. Nao ha emulador rodando junto com o programa.

Comandos:
  scan    <arquivo>                 So analisa: quantas instrucoes F16C existem.
  patch   <arquivo> [-o SAIDA]      Gera uma copia convertida (padrao: <nome>_f16c.exe).
  patch   <arquivo> --in-place      Troca o original pela versao convertida e
                                    guarda o original como <arquivo>.f16c-backup.
  restore <arquivo>                 Desfaz o --in-place usando o backup.

Opcoes de patch:
  --no-cpuid      Nao mexe na pergunta "este processador tem F16C?" (CPUID).
  --aggressive    Converte tambem instrucoes achadas apenas por varredura de
                  bytes (nao confirmadas pelo fluxo do programa). Use se o
                  programa ainda fechar com "instrucao ilegal".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "core" / "pylibs"), str(ROOT / "core")]

NAME = "FX-Reborn-F16C-Ultra"
VERSION = "3.0.0"
BACKUP_SUFFIX = ".f16c-backup"


def say(*parts):
    print(*parts, flush=True)


class _Progress:
    """Turns the code finder's technical progress lines into readable ones."""

    def __init__(self, out):
        self.out, self.pending = out, ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            try:
                info = json.loads(line)
                line = (f"   ...{info['instructions'] / 1e6:.1f} milhoes de instrucoes analisadas "
                        f"({info['elapsed_s']:.0f}s)")
            except (ValueError, KeyError, TypeError):
                pass
            self.out.write(line + "\n")
            self.out.flush()
        return len(text)

    def flush(self):
        self.out.flush()


def analyse(path: Path):
    """Returns (pe, data, confirmed sites, unconfirmed sites)."""
    import iced_x86 as ix
    import f16c_lower
    import pe_patch

    pe, data = pe_patch.load(path)
    kind = "DLL" if pe.FILE_HEADER.Characteristics & 0x2000 else "EXE"
    say(f"Arquivo: {path.name} ({kind} 64 bits, {len(data) / 1e6:.1f} MB)")
    if pe_patch.is_patched(pe):
        raise pe_patch.PatchError("este arquivo ja foi convertido (tem a secao .f16c). "
                                  "Use o original, ou 'Restaurar original'.")

    say("Etapa 1/2: procurando instrucoes F16C (varredura do codigo)...")
    started = time.time()
    swept = pe_patch.sweep(pe, lambda name, pos, total: say(f"   {name}: {100 * pos // max(total, 1)}%"))
    say(f"   {len(swept)} encontrada(s) em {time.time() - started:.0f}s")
    if not swept:
        return pe, data, [], []

    say("Etapa 2/2: confirmando pelo fluxo do programa (em jogos grandes leva alguns minutos)...")
    started = time.time()
    import contextlib
    import find_sites
    with contextlib.redirect_stdout(_Progress(sys.stdout)):
        discovery = find_sites.discover(path)
    reached, _ = find_sites.select(pe, discovery)
    base = pe.OPTIONAL_HEADER.ImageBase
    confirmed = []
    for rva in reached:
        ins = ix.Decoder(64, pe.get_data(rva, 15), ip=base + rva).decode()
        if f16c_lower.is_f16c(ins):
            confirmed.append(rva)
    say(f"   pronto em {time.time() - started:.0f}s")
    unconfirmed = sorted(set(swept) - set(confirmed))
    return pe, data, sorted(confirmed), unconfirmed


def summary(pe, confirmed, unconfirmed):
    import collections
    import iced_x86 as ix
    fmt = ix.Formatter(ix.FormatterSyntax.INTEL)
    base = pe.OPTIONAL_HEADER.ImageBase
    counts = collections.Counter()
    for rva in confirmed + unconfirmed:
        ins = ix.Decoder(64, pe.get_data(rva, 15), ip=base + rva).decode()
        counts[fmt.format_mnemonic(ins)] += 1
    say("")
    say(f"Instrucoes F16C confirmadas: {len(confirmed)}")
    say(f"Nao confirmadas (so varredura; podem ser dados e nao codigo): {len(unconfirmed)}")
    for name, n in counts.most_common():
        say(f"   {name}: {n}")


def cmd_scan(a):
    pe, _, confirmed, unconfirmed = analyse(Path(a.file))
    if not confirmed and not unconfirmed:
        say("\nNenhuma instrucao F16C neste arquivo. Nao ha nada para converter.")
        return 0
    summary(pe, confirmed, unconfirmed)
    say("\nPara converter, use 'Aplicar' (ou o comando patch).")
    return 0


def cmd_patch(a):
    import pe_patch
    path = Path(a.file).resolve()
    pe, data, confirmed, unconfirmed = analyse(path)
    if not confirmed and not unconfirmed:
        say("\nNenhuma instrucao F16C neste arquivo. Nada foi alterado.")
        return 0
    summary(pe, confirmed, unconfirmed)
    sites = confirmed + (unconfirmed if a.aggressive else [])
    cpuid = []
    if not a.no_cpuid:
        say("\nProcurando as checagens de CPU do programa (CPUID)...")
        cpuid = pe_patch.cpuid_sites(pe)
        say(f"   {len(cpuid)} ponto(s) onde o programa consulta o processador")
    if not sites:
        say("\nNenhuma instrucao confirmada. Tente de novo com o modo agressivo.")
        return 1

    say(f"\nConvertendo {len(sites)} instrucao(oes)...")
    patched, report = pe_patch.patch(pe, data, sites, cpuid)
    if not report["patched"]:
        say("Nada pode ser convertido:")
        for s in report["skipped"]:
            say(f"   {s['rva']}: {s['reason']}")
        return 1

    if a.in_place:
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        output = path
    else:
        output = Path(a.out).resolve() if a.out else path.with_name(f"{path.stem}_f16c{path.suffix}")
        if output == path:
            raise pe_patch.PatchError("a saida nao pode ser o proprio arquivo; use --in-place")
    temporary = output.with_name(output.name + ".f16c-tmp")
    temporary.write_bytes(patched)
    if a.in_place:
        if backup.exists():
            raise pe_patch.PatchError(f"ja existe um backup ({backup.name}); restaure-o ou apague-o antes")
        os.replace(path, backup)
        say(f"Original guardado em: {backup}")
    os.replace(temporary, output)

    report.update(tool=f"{NAME} {VERSION}", source=str(path), source_sha256=hashlib.sha256(data).hexdigest(),
                  output=str(output), output_sha256=hashlib.sha256(patched).hexdigest(),
                  aggressive=bool(a.aggressive), unconfirmed_left=[] if a.aggressive else [hex(r) for r in unconfirmed])
    report_path = output.with_name(output.name + ".f16c-report.json")
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")

    say(f"\nPRONTO. Convertidas: {report['patched']}   Ignoradas: {len(report['skipped'])}")
    if not a.no_cpuid:
        say(f"Consultas ao processador ajustadas para responder 'tem F16C': {len(report['cpuid_patched'])}")
    for s in report["skipped"]:
        say(f"   ignorada {s['rva']}: {s['reason']}")
    if unconfirmed and not a.aggressive:
        say(f"Atencao: {len(unconfirmed)} instrucao(oes) nao confirmada(s) ficaram como estavam.")
        say("   Se o programa fechar com 'instrucao ilegal', refaca com o modo agressivo.")
    if report.get("signature_removed"):
        say("A assinatura digital do arquivo foi removida (ela deixaria de valer de qualquer forma).")
    say(f"Arquivo convertido: {output}")
    say(f"Relatorio: {report_path}")
    return 0


def cmd_restore(a):
    path = Path(a.file).resolve()
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        say(f"Nao ha backup para restaurar ({backup.name} nao existe).")
        return 1
    os.replace(backup, path)
    report = path.with_name(path.name + ".f16c-report.json")
    if report.exists():
        report.unlink()
    say(f"Original restaurado: {path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("file")
    p = sub.add_parser("patch")
    p.add_argument("file")
    p.add_argument("-o", "--out")
    p.add_argument("--in-place", action="store_true")
    p.add_argument("--aggressive", action="store_true")
    p.add_argument("--no-cpuid", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("file")
    a = ap.parse_args()
    say(f"{NAME} {VERSION}")
    from pe_patch import PatchError
    try:
        return {"scan": cmd_scan, "patch": cmd_patch, "restore": cmd_restore}[a.cmd](a)
    except PatchError as err:
        say(f"\nERRO: {err}")
        return 2
    except (OSError, PermissionError) as err:
        say(f"\nERRO de arquivo: {err}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
