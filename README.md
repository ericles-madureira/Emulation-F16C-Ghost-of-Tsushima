# FX-Reborn-F16C-Ultra 3.0

**Correção estática de instruções F16C para processadores antigos (Ghost of Tsushima e outros jogos x64)**  
*(anteriormente F16C-Emu)*

[🇧🇷 Português](#português) · [🇺🇸 English](#english)

> **Seu jogo fecha com erro de *"instrução ilegal"* (`0xC000001D`) ou avisa que o processador não é suportado?**  
> Se o seu processador possui **AVX**, mas não tem **F16C** (como Intel Sandy Bridge `i5-2400`, `i5-2500K`, `i7-2600`, `i7-2700K` ou AMD Bulldozer `FX-4100`, `FX-6100`, `FX-8120`, `FX-8150`), esta ferramenta reescreve o executável para que ele funcione normalmente. Criada originalmente para **Ghost of Tsushima (PC)** e compatível com outros programas x64 que exigem F16C.

*A interface gráfica está disponível em **Português** e **Inglês** (detecta o idioma do Windows automaticamente ou pode ser alternada no canto superior direito).*

---

## Português

O **FX-Reborn-F16C-Ultra** não é um emulador rodando em segundo plano. Ele **modifica o executável uma única vez**, substituindo cada instrução F16C por um desvio (`JMP`) para uma rotina equivalente em AVX padrão, gravada em uma nova seção `.f16c` dentro do próprio arquivo. Depois de aplicado, o jogo roda de forma nativa, sem DLLs extras e sem processos paralelos.

As duas únicas instruções do conjunto F16C são suportadas:

| Instrução | Função |
| :--- | :--- |
| `VCVTPH2PS` | Converte ponto flutuante de 16 bits (*half*) para 32 bits (*single*) |
| `VCVTPS2PH` | Converte ponto flutuante de 32 bits (*single*) para 16 bits (*half*) |

### Novidades da versão 3.0

Os resultados continuam idênticos bit a bit aos do hardware real, mas o código gerado foi otimizado para reduzir o custo por conversão e liberar barramentos de memória:

- **Análise de registradores livres (`vector_liveness`):** Nas versões anteriores, cada rotina salvava e restaurava até 7 registradores vetoriais de 32 bytes na pilha. Agora, a ferramenta analisa o fluxo seguinte (incluindo desvios, ramificações `if` e laços) e reutiliza gratuitamente registradores que o próprio programa vai sobrescrever antes de ler — zerando o acesso à pilha quando há registradores livres.
- **Salvamento parcial de 128 bits:** Quando apenas a metade superior de um registrador YMM é descartada em seguida (como antes de um `vzeroupper`, comum em jogos), salva-se apenas a metade inferior (XMM), reduzindo o tráfego de memória pela metade e evitando escritas que cruzam linhas de cache.
- **Dispensa de leitura do `MXCSR` no caso comum:** Conversões *float → half* com arredondamento padrão (*round-to-nearest*, usado por jogos e DirectXMath) ou truncamento não dependem do modo DAZ, eliminando a leitura do registrador de controle (`VSTMXCSR`) e economizando 7 instruções e 32 bytes de pilha por chamada.

#### Desempenho medido em um Intel Core i7-2600 (por instrução, v0.2 → v3.0)

| Instrução / Forma | Registradores ocupados | Registradores livres |
| :--- | :---: | :---: |
| `vcvtps2ph xmm, ymm, 0` (8 valores) | 12,9 ns → **11,0 ns** | 12,5 ns → **8,8 ns** |
| `vcvtps2ph [mem], xmm, 4` | 8,5 ns → **8,4 ns** | 8,5 ns → **6,8 ns** |
| `vcvtph2ps ymm, xmm` | 5,4 ns → **5,6 ns** | 5,4 ns → **4,8 ns** |

> [!TIP]
> **Atualizando de uma versão anterior?** Abra a ferramenta, selecione o executável já modificado, clique em **Restaurar original** e aplique o patch novamente para obter as melhorias da versão 3.0.

### Como usar (Interface Gráfica)

1. **Feche** completamente o jogo ou programa.
2. Execute **`Open- Abrir FX-Reborn-F16C-Ultra.cmd`**.
3. Clique em **Procurar...** e selecione o `.exe` desejado (ou arraste o arquivo para a janela).
4. Escolha o modo de saída:
   - **Substituir o arquivo (padrão):** modifica o arquivo original e cria o backup `nome.exe.f16c-backup`.
   - **Criar uma cópia:** gera `nome_f16c.exe` na mesma pasta sem alterar o original.
5. Clique em **3. Aplicar** e aguarde a mensagem `PRONTO` (arquivos acima de 100 MB podem levar alguns minutos).
6. Inicie o jogo normalmente.

Para desfazer qualquer alteração, selecione o mesmo arquivo e clique em **Restaurar original**. A pasta já inclui Python 3.13 e todas as dependências necessárias — nada é instalado no sistema nem enviado à internet.

#### O jogo ainda fecha com "instrução ilegal"?

- **Ative o Modo agressivo:** Restaure o original, marque **Modo agressivo** e aplique novamente. Por segurança, o modo padrão converte apenas instruções alcançadas pelo fluxo de controle; o modo agressivo converte também ocorrências encontradas via varredura bruta de bytes.
- **Verifique DLLs do jogo:** Jogos costumam conter instruções F16C em bibliotecas `.dll` na mesma pasta. Use **Só analisar** para inspecionar e converta as DLLs que acusarem F16C.
- **Outras instruções ausentes (AVX2, FMA, BMI):** Esta ferramenta trata exclusivamente **F16C**. Se o jogo também exigir AVX2, FMA3, BMI1/2, LZCNT ou MOVBE (como *Final Fantasy VII Rebirth*), utilize o **[FX Reborn Toolkit Ultra 3.0](https://github.com/mcirish3/FX-Reborn-Toolkit)**.

### Linha de comando (CLI)

```bat
f16c-cli scan    "C:\pasta\Programa.exe"
f16c-cli patch   "C:\pasta\Programa.exe"                 :: gera Programa_f16c.exe
f16c-cli patch   "C:\pasta\Programa.exe" -o "D:\saida\Programa.exe"
f16c-cli patch   "C:\pasta\Programa.exe" --in-place      :: substitui o original e cria backup
f16c-cli patch   "C:\pasta\Programa.exe" --aggressive    :: inclui varredura bruta de bytes
f16c-cli patch   "C:\pasta\Programa.exe" --no-cpuid      :: não altera verificações de CPUID
f16c-cli restore "C:\pasta\Programa.exe"
```

Ao lado do executável modificado é gerado um arquivo `…f16c-report.json` detalhando todos os endereços alterados.

### Requisitos e limitações

> [!WARNING]
> **Não utilize em jogos online com anti-cheat.** Modificar executáveis ou DLLs dispara verificações de integridade e pode resultar em banimento da conta.

- **Sistema e hardware:** Windows 64 bits, binários x64 (`.exe` / `.dll`) e processador com suporte a **AVX**.
- **Verificações de CPU (`CPUID`):** Jogos que consultam o processador antes de abrir e exibem erro de incompatibilidade têm suas verificações seguras ajustadas automaticamente para reportar suporte a F16C (desativável via `--no-cpuid`).
- **DRM e assinatura digital:** A assinatura digital do binário é removida após a modificação. Executáveis protegidos por DRM com verificação própria de integridade ou empacotadores podem falhar.
- **Atualizações de launchers:** Atualizações ou verificações de integridade na Steam/Epic Games restauram os arquivos originais; basta reaplicar o patch.
- **Fidelidade numérica:** Resultados idênticos bit a bit ao hardware (incluindo os 4 modos de arredondamento, `NaN`, infinitos, subnormais e DAZ/FTZ). Apenas as flags de exceção do `MXCSR` (*overflow*, *inexact*, etc.) não são atualizadas, o que não afeta jogos ou aplicações comuns.

### Arquitetura interna

1. **Descoberta (`core/pe_patch.py`, `core/find_sites.py`, `core/discover_code_sites.py`):** Mapeia candidatas e percorre o fluxo de execução a partir do *Entry Point*, exportações e tabelas de exceção para distinguir código real de dados.
2. **Tradução (`core/f16c_lower.py`):** Gera rotinas vetoriais em AVX puro sem alterar flags ou registradores de uso geral. O analisador `vector_liveness` percorre até 400 instruções à frente (parando em `call`, `ret` e saltos indiretos) para minimizar salvamentos na pilha. As constantes são agrupadas em uma tabela única compartilhada.
3. **Injeção PE (`core/pe_patch.py`):** Substitui a instrução original (sempre ≥ 5 bytes) por um `JMP` para a nova seção `.f16c` (ou final da última seção caso o cabeçalho PE não tenha espaço), atualiza os metadados de *unwind* do Windows para tratamento de exceções e recalcula o *checksum* do cabeçalho.

### Testes e validação

```bat
:: Validação exaustiva (todos os 65.536 valores half, modos de arredondamento, DAZ/FTZ e preservação de registradores)
python\python.exe tests\test_lower.py

:: Benchmark de latência por instrução (aceita outra versão de f16c_lower.py como argumento para comparação)
python\python.exe tests\bench_lower.py

:: Teste ponta a ponta e validação de unwind table do Windows
gcc -O2 -mavx -mf16c -static -o f16c_test.exe tests\f16c_test.c
f16c-cli patch f16c_test.exe
f16c_test_f16c.exe
python\python.exe tests\test_unwind.py f16c_test_f16c.exe
```

### Créditos e licença

Distribuído sob a licença **MIT** (consulte `LICENSE`).

- **Descoberta de código e conceito:** Baseado no [FX Reborn Toolkit](https://github.com/mcirish3/FX-Reborn-Toolkit) de Patrick McCoy (MIT).
- **Dependências incluídas:** [iced-x86](https://github.com/icedland/iced) (MIT), [pefile](https://github.com/erocarrera/pefile) (MIT) e Python 3.13 (PSF License).
- **Aviso legal:** Compartilhe apenas esta ferramenta. **Nunca distribua executáveis ou DLLs de jogos**, originais ou modificados.

---

## English

**FX-Reborn-F16C-Ultra** statically rewrites Windows x64 executables **once** so they run on CPUs with **AVX** that lack **F16C** (such as Intel Sandy Bridge `i5-2400`, `i5-2500K`, `i7-2600`, `i7-2700K` or AMD Bulldozer `FX-4100`, `FX-6100`, `FX-8120`, `FX-8150`).

Each F16C instruction (`VCVTPH2PS` and `VCVTPS2PH`) is replaced with a `JMP` to an equivalent plain-AVX routine stored in a new `.f16c` PE section. There is no background emulator, no extra DLL, and results are bit-for-bit identical to native hardware (including rounding modes, `NaN`, infinities, subnormals, and DAZ/FTZ).

### What's new in 3.0

- **Dead-register liveness analysis (`vector_liveness`):** Traces up to 400 instructions ahead across branches and loops to reuse vector registers that the program overwrites before reading, eliminating up to 7 × 32-byte stack spills per instruction.
- **128-bit partial saves:** When the upper 128 bits of a YMM register are dead (e.g., before `vzeroupper`), only the lower XMM half is saved, halving memory traffic and avoiding cache-line-splitting stores.
- **No `VSTMXCSR` read in common rounding modes:** Round-to-nearest and truncate modes skip reading `MXCSR`, saving 7 instructions and 32 bytes of stack space per call.

| Instruction | Busy registers (v0.2 → v3.0) | Free registers (v0.2 → v3.0) |
| :--- | :---: | :---: |
| `vcvtps2ph xmm, ymm, 0` (8 floats) | 12.9 ns → **11.0 ns** | 12.5 ns → **8.8 ns** |
| `vcvtps2ph [mem], xmm, 4` | 8.5 ns → **8.4 ns** | 8.5 ns → **6.8 ns** |
| `vcvtph2ps ymm, xmm` | 5.4 ns → **5.6 ns** | 5.4 ns → **4.8 ns** |

*(Measured on an Intel Core i7-2600. If you patched a game with an older version, click **Restore original** first and re-apply.)*

### How to use

1. **Close** the game completely.
2. Double-click **`Open- Abrir FX-Reborn-F16C-Ultra.cmd`** (defaults to English on non-Portuguese Windows; switchable via the top-right *Language* menu).
3. Click **Browse...** and select the game `.exe` (or drag and drop it into the window).
4. Pick **Replace the file** (creates `name.exe.f16c-backup`) or **Create a converted copy** (`name_f16c.exe`).
5. Click **3. Apply** and wait for `PRONTO` (done). Large binaries may take a few minutes.
6. Launch the game normally. To revert, select the same file and click **Restore original**.

Everything is self-contained (Python 3.13 and libraries are bundled) and runs 100% offline.

**Still crashing with `0xC000001D` / Illegal Instruction?**
- Restore the original, enable **Aggressive mode**, and patch again (patches byte-scan candidates outside standard control-flow discovery).
- Scan and patch game `.dll` files that contain F16C instructions.
- If the game also requires **AVX2, FMA3, BMI1/2, LZCNT, or MOVBE**, use **[FX Reborn Toolkit Ultra 3.0](https://github.com/mcirish3/FX-Reborn-Toolkit)**.

### Command Line

```bat
f16c-cli scan    "C:\path\Game.exe"
f16c-cli patch   "C:\path\Game.exe"
f16c-cli patch   "C:\path\Game.exe" -o "D:\out\Game.exe"
f16c-cli patch   "C:\path\Game.exe" --in-place
f16c-cli patch   "C:\path\Game.exe" --aggressive --no-cpuid
f16c-cli restore "C:\path\Game.exe"
```

### Requirements & Warnings

- **Requirements:** 64-bit Windows, x64 `.exe`/`.dll`, and a CPU with **AVX**. Safe `CPUID` checks are patched automatically unless `--no-cpuid` is passed.
- **Anti-cheat & DRM:** Do not use on online games with anti-cheat (risk of ban). Digital signatures are stripped, and binaries with strict self-integrity DRM/packers may not launch.
- **Game updates:** Steam/Epic updates or file verifications will overwrite patched files — simply re-apply.
- **License & Credits:** MIT License. Code discovery logic from [FX Reborn Toolkit](https://github.com/mcirish3/FX-Reborn-Toolkit) by Patrick McCoy (MIT), powered by [iced-x86](https://github.com/icedland/iced) and [pefile](https://github.com/erocarrera/pefile). **Never distribute game executables — only this tool.**