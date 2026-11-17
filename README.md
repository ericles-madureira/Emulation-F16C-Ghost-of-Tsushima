# FX-Reborn-F16C-Ultra 3.0

*(antes chamado F16C-Emu)*

Faz um programa de Windows que **exige F16C** rodar em processadores que **não têm F16C** (por exemplo Intel Sandy Bridge: i5-2400, i7-2600; AMD FX-4100/6100/8100/8150).

Ele não é um emulador rodando junto com o programa. Ele **reescreve o executável uma única vez**: cada instrução F16C é trocada por um desvio para um trecho de código equivalente, gravado numa seção nova do próprio arquivo (`.f16c`). Depois disso o programa roda sozinho, sem DLL extra.

O F16C tem só duas instruções, e as duas são convertidas:

| Instrução | O que faz |
|---|---|
| `VCVTPH2PS` | número de 16 bits ("half") → float de 32 bits |
| `VCVTPS2PH` | float de 32 bits → número de 16 bits ("half") |

## Novidades da 3.0: mais rápido e menos trabalho para o processador

O código que substitui cada instrução F16C foi enxugado. Os resultados continuam idênticos bit a bit; o que mudou é quanto trabalho o processador faz a cada conversão:

- **Registradores livres não são mais salvos.** Antes, todo trecho convertido guardava na pilha (memória) os registradores vetoriais que usava como rascunho e depois os recarregava: até 7 gravações e 7 leituras de 32 bytes por instrução. Agora a ferramenta examina o código que vem depois de cada instrução (seguindo os desvios, os dois lados de cada `if` e os laços) e descobre quais registradores o próprio programa vai sobrescrever antes de ler. Esses são usados de graça, sem salvar nada. Quando todos estão livres, o trecho não toca na pilha.
- **Meia gravação quando basta.** Se só a metade superior (128 bits) de um registrador é descartada depois (por exemplo, há um `vzeroupper` logo a seguir, o que é comum em jogos), salva-se só a metade inferior: metade da memória, e sem gravações de 32 bytes que cruzam linha de cache.
- **Sem leitura do MXCSR no caso comum.** A conversão float → half com arredondamento "ao mais próximo" (o que jogos e o DirectXMath usam) ou "truncar" não depende do modo DAZ, então o trecho deixou de ler o registrador de controle (`VSTMXCSR`) e de montar o limiar a cada chamada: 7 instruções e 32 bytes de pilha a menos.

Medido num i7-2600 (sem F16C), por instrução convertida, versão 0.2 → 3.0:

| Forma | Registradores ocupados | Livres depois |
|---|---|---|
| `vcvtps2ph xmm, ymm, 0` (8 valores) | 12,9 → 11,0 ns | 12,5 → **8,8 ns** |
| `vcvtps2ph [mem], xmm, 4` | 8,5 → 8,4 ns | 8,5 → **6,8 ns** |
| `vcvtph2ps ymm, xmm` | 5,4 → 5,6 ns | 5,4 → **4,8 ns** |

Além do tempo, cada trecho que não salva registradores deixa de ocupar as portas de leitura/gravação de memória que o resto do jogo está usando ao mesmo tempo. Quanto isso vira de FPS depende de quantas conversões F16C o jogo faz por quadro: onde elas pesam, o ganho aparece; onde são raras, a diferença é pequena.

**Quem já converteu um jogo com versão anterior:** clique em *Restaurar original* e aplique de novo para usar o código novo.

## Como usar (janela)

1. **Feche** o jogo/programa.
2. Dê dois cliques em **`Abrir FX-Reborn-F16C-Ultra.cmd`**.
3. Clique em **Procurar...** e escolha o `.exe` (ou arraste o arquivo para a caixa).
4. Escolha como salvar:
   - **Substituir o arquivo** (padrão): o original é guardado ao lado como `nome.exe.f16c-backup`.
   - **Criar uma cópia**: gera `nome_f16c.exe` ao lado e não toca no original.
5. Clique em **3. Aplicar** e espere aparecer `PRONTO`. Em jogos grandes (100 MB+) leva alguns minutos.
6. Abra o programa normalmente.

Para desfazer: escolha o mesmo arquivo e clique em **Restaurar original**.

Não precisa instalar nada: a pasta já traz o Python e as bibliotecas. Nada é enviado para a internet.

### Se o programa ainda fechar com "instrução ilegal"

- Marque **Modo agressivo** e aplique de novo (antes, clique em *Restaurar original*). Por segurança, o modo normal só converte instruções que ele confirmou serem código seguindo o fluxo do programa; o agressivo converte também as que só apareceram numa varredura de bytes.
- O programa pode precisar de **outras** instruções que seu processador não tem (AVX2, FMA, BMI...). Este projeto, por enquanto, trata **somente F16C**. Clique em **Só analisar** para ver quantas instruções F16C o arquivo tem.
- O jogo pode ter instruções F16C também em **DLLs**. Converta cada uma do mesmo jeito.

## Linha de comando

```bat
f16c-cli scan    "C:\pasta\Programa.exe"
f16c-cli patch   "C:\pasta\Programa.exe"                 :: cria Programa_f16c.exe
f16c-cli patch   "C:\pasta\Programa.exe" -o "D:\saida\Programa.exe"
f16c-cli patch   "C:\pasta\Programa.exe" --in-place      :: troca o original, guarda backup
f16c-cli patch   "C:\pasta\Programa.exe" --aggressive
f16c-cli restore "C:\pasta\Programa.exe"
```

Junto do arquivo convertido é gravado um `…f16c-report.json` com a lista de tudo que foi alterado.

## Requisitos e limites

- **Windows 64 bits**, executáveis **x64** (`.exe` ou `.dll`).
- O processador precisa ter **AVX**. Na prática todo programa que usa F16C também usa AVX, então isso não muda nada: o que deixa de ser exigido é só o F16C.
- **Só F16C.** Se o programa também exigir AVX2/FMA/BMI, ele continuará sem abrir em processadores que não os têm.
- **Verificação de CPU:** alguns programas perguntam ao processador "você tem F16C?" e se recusam a abrir com uma mensagem própria. A ferramenta trata isso também: os pontos onde o programa consulta o processador (`CPUID`) passam a responder "tem F16C". Só são alterados os pontos em que isso é claramente seguro; se a mensagem continuar aparecendo, a consulta está num ponto que a ferramenta não alcançou. A opção `--no-cpuid` da linha de comando desliga esse ajuste.
- **Jogos online / anti-cheat:** um executável modificado pode ser detectado por anti-cheat ou verificações de integridade e causar banimento. Não use em jogos com anti-cheat.
- **Executáveis protegidos** (DRM que confere o próprio arquivo, empacotadores) podem não funcionar depois de modificados.
- A **assinatura digital** do arquivo, se existir, é removida (ela deixaria de valer de qualquer forma).
- Launchers (Steam, Epic) podem "reparar" o arquivo numa atualização ou verificação; basta aplicar de novo.
- O código convertido é mais lento que a instrução real, mas é vetorial: converte os 4 ou 8 valores de uma vez, sem desvios, e não sofre a lentidão de valores "denormais". Num i7-2600, cada instrução convertida leva cerca de 5 a 11 ns (a 0.2 levava 5 a 13 ns e a 0.1, 13 a 32 ns). Para o uso típico de F16C em jogos (compactar/descompactar dados) isso não costuma aparecer.
- **Atualizando de uma versão anterior:** para o jogo usar o código novo, clique em *Restaurar original* e aplique de novo.
- Fidelidade: os resultados são idênticos bit a bit aos da instrução real, incluindo arredondamento (os 4 modos, fixo ou vindo do MXCSR), infinitos, NaN, subnormais e DAZ. A única diferença é que os bits de *status* de exceção do MXCSR (inexato, overflow...) não são atualizados como na instrução real; programas normais não os leem.

## Como funciona

1. **Localizar** (`core/pe_patch.py`, `core/find_sites.py`, `core/discover_code_sites.py`): uma varredura acha as candidatas; depois o programa é percorrido a partir do ponto de entrada, das funções exportadas e da tabela de funções, seguindo os desvios, para confirmar quais candidatas são mesmo código e não dados.
2. **Traduzir** (`core/f16c_lower.py`): para cada instrução é gerado um trecho que faz a mesma conversão com AVX comum (sem AVX2/FMA), em todos os elementos ao mesmo tempo e sem desvios. Ele não usa registradores gerais nem altera as flags. Para os registradores vetoriais de rascunho, `vector_liveness` percorre o código seguinte (até 400 instruções, por todos os caminhos de desvios diretos e laços; para em `call`, `ret` e desvios indiretos) e separa os que o programa sobrescreve antes de ler: esses são usados sem salvar; os que só têm a metade superior descartada têm só a metade inferior salva; os demais são guardados inteiros na pilha e restaurados. Só o destino da instrução original muda de valor do ponto de vista do programa. As constantes ficam numa tabela única, compartilhada por todos os trechos.
3. **Gravar** (`core/pe_patch.py`): a instrução (sempre 5 bytes ou mais) vira um `JMP` para o trecho novo, que termina voltando para a instrução seguinte. Os trechos ficam numa seção nova `.f16c` (ou no fim da última seção, quando o cabeçalho do arquivo não tem espaço para mais uma); também são gravadas as informações de pilha que o Windows usa para tratar exceções, e o checksum do arquivo é recalculado.

## Testes

```bat
python\python.exe tests\test_lower.py
```
Executa o código gerado neste computador e compara com uma referência independente: todos os 65.536 valores de half e milhares de floats de borda e aleatórios, em todos os modos de arredondamento (também com DAZ/FTZ e modo do MXCSR diferente do pedido), com operandos em registrador e em memória. Também confere que nenhum outro registrador (gerais, flags, YMM0–YMM15) muda, que os trechos que usam registradores livres só alteram exatamente os registradores (ou metades) liberados, e que a análise de registradores livres acerta em sequências com desvios, laços, `call`, `vzeroupper` e escrita SSE antiga.

```bat
python\python.exe tests\bench_lower.py
```
Mede quantos nanossegundos cada forma convertida leva (registradores ocupados, só a metade superior livre, todos livres, e com floats denormais). Passe outra versão de `f16c_lower.py` como argumento para comparar lado a lado.

```bat
gcc -O2 -mavx -mf16c -static -o f16c_test.exe tests\f16c_test.c
f16c-cli patch f16c_test.exe
f16c_test_f16c.exe
python\python.exe tests\test_unwind.py f16c_test_f16c.exe
```
`f16c_test.exe` é compilado **com** F16C: num processador sem F16C o original morre com "illegal instruction" e o convertido precisa imprimir `PASS`. O `test_unwind.py` confere as informações de pilha com o próprio desenrolador do Windows.

## Licença e créditos

MIT (veja `LICENSE`). Projeto de código aberto.

- O localizador de código (`core/discover_code_sites.py`, `core/find_sites.py`) vem do [FX Reborn Toolkit](https://github.com/mcirish3/FX-Reborn-Toolkit) de Patrick McCoy (MIT), usado sem alterações. A ideia geral (traduzir antes, dentro do executável) também vem dele.
- Bibliotecas: [iced-x86](https://github.com/icedland/iced) e [pefile](https://github.com/erocarrera/pefile) (MIT). O Python 3.13 incluído é distribuído sob a licença PSF.
- Distribua apenas esta ferramenta. **Nunca distribua executáveis de jogos**, originais ou convertidos.
