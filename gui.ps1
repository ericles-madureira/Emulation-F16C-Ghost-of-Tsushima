# FX-Reborn-F16C-Ultra 3.0 - janela simples. Aberta por "Abrir FX-Reborn-F16C-Ultra.cmd".
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'python\python.exe'
$script = Join-Path $root 'f16c_emu.py'

$form = New-Object System.Windows.Forms.Form
$form.Text = 'FX-Reborn-F16C-Ultra 3.0'
$form.ClientSize = New-Object System.Drawing.Size(720, 520)
$form.MinimumSize = New-Object System.Drawing.Size(640, 440)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9.5)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Faz um programa que exige F16C rodar em processadores sem F16C'
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 11)
$title.SetBounds(14, 12, 692, 24)
$title.Anchor = 'Top,Left,Right'

$step1 = New-Object System.Windows.Forms.Label
$step1.Text = '1. Escolha o executável (.exe) ou a DLL:'
$step1.SetBounds(14, 48, 500, 20)

$pathBox = New-Object System.Windows.Forms.TextBox
$pathBox.SetBounds(14, 70, 580, 26)
$pathBox.Anchor = 'Top,Left,Right'
$pathBox.AllowDrop = $true

$browse = New-Object System.Windows.Forms.Button
$browse.Text = 'Procurar...'
$browse.SetBounds(602, 68, 104, 28)
$browse.Anchor = 'Top,Right'

$step2 = New-Object System.Windows.Forms.Label
$step2.Text = '2. Como salvar:'
$step2.SetBounds(14, 108, 500, 20)

$optReplace = New-Object System.Windows.Forms.RadioButton
$optReplace.Text = 'Substituir o arquivo (o original fica guardado como backup ao lado)'
$optReplace.SetBounds(28, 130, 660, 22)
$optReplace.Checked = $true

$optCopy = New-Object System.Windows.Forms.RadioButton
$optCopy.Text = 'Criar uma cópia convertida ao lado (nome_f16c.exe), sem mexer no original'
$optCopy.SetBounds(28, 154, 660, 22)

$aggressive = New-Object System.Windows.Forms.CheckBox
$aggressive.Text = 'Modo agressivo (só marque se o programa ainda fechar com "instrução ilegal")'
$aggressive.SetBounds(28, 182, 660, 22)

$apply = New-Object System.Windows.Forms.Button
$apply.Text = '3. Aplicar'
$apply.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 10.5)
$apply.SetBounds(14, 216, 170, 36)

$scan = New-Object System.Windows.Forms.Button
$scan.Text = 'Só analisar'
$scan.SetBounds(194, 216, 130, 36)

$restore = New-Object System.Windows.Forms.Button
$restore.Text = 'Restaurar original'
$restore.SetBounds(334, 216, 160, 36)

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = 'Vertical'
$log.Font = New-Object System.Drawing.Font('Consolas', 9.5)
$log.BackColor = [System.Drawing.Color]::White
$log.SetBounds(14, 264, 692, 214)
$log.Anchor = 'Top,Bottom,Left,Right'

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Pronto.'
$status.SetBounds(14, 488, 692, 22)
$status.Anchor = 'Bottom,Left,Right'

$form.Controls.AddRange(@($title, $step1, $pathBox, $browse, $step2, $optReplace, $optCopy,
                          $aggressive, $apply, $scan, $restore, $log, $status))

function Write-Log([string]$text) {
    $log.AppendText($text + "`r`n")
}

function Invoke-Tool([string[]]$toolArgs, [string]$busyText) {
    $file = $pathBox.Text.Trim().Trim('"')
    if (-not $file) {
        [System.Windows.Forms.MessageBox]::Show('Escolha um arquivo primeiro.', 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    if (-not (Test-Path -LiteralPath $file)) {
        [System.Windows.Forms.MessageBox]::Show("Arquivo não encontrado:`n$file", 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    if (-not (Test-Path -LiteralPath $python)) {
        [System.Windows.Forms.MessageBox]::Show("Não achei $python.`nMantenha a pasta 'python' junto do programa.", 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    $log.Clear()
    $buttons = @($apply, $scan, $restore, $browse)
    foreach ($b in $buttons) { $b.Enabled = $false }
    $status.Text = $busyText
    $form.Cursor = 'WaitCursor'

    $quoted = @("`"$script`"") + $toolArgs + @("`"$file`"")
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $python
    $info.Arguments = '-X utf8 -u ' + ($quoted -join ' ')
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    $errTask = $process.StandardError.ReadToEndAsync()
    $lineTask = $process.StandardOutput.ReadLineAsync()
    while ($true) {
        if ($lineTask.IsCompleted) {
            $line = $lineTask.Result
            if ($null -eq $line) { break }
            if (-not $line.StartsWith('{')) { Write-Log $line }     # pula linhas tecnicas de progresso
            $lineTask = $process.StandardOutput.ReadLineAsync()
        } else {
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 30
        }
    }
    $process.WaitForExit()
    $err = $errTask.Result
    if ($err.Trim()) { Write-Log ''; Write-Log 'Detalhes do erro:'; Write-Log $err.Trim() }

    $form.Cursor = 'Default'
    foreach ($b in $buttons) { $b.Enabled = $true }
    if ($process.ExitCode -eq 0) { $status.Text = 'Concluído.' }
    else { $status.Text = 'Terminou com erro - veja as mensagens acima.' }
}

$browse.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Escolha o executável'
    $dialog.Filter = 'Programas (*.exe;*.dll)|*.exe;*.dll|Todos os arquivos (*.*)|*.*'
    if ($dialog.ShowDialog($form) -eq 'OK') { $pathBox.Text = $dialog.FileName }
})

$pathBox.Add_DragEnter({ param($s, $e)
    if ($e.Data.GetDataPresent([System.Windows.Forms.DataFormats]::FileDrop)) { $e.Effect = 'Copy' }
})
$pathBox.Add_DragDrop({ param($s, $e)
    $files = $e.Data.GetData([System.Windows.Forms.DataFormats]::FileDrop)
    if ($files) { $pathBox.Text = $files[0] }
})

$apply.Add_Click({
    $toolArgs = @('patch')
    if ($optReplace.Checked) { $toolArgs += '--in-place' }
    if ($aggressive.Checked) { $toolArgs += '--aggressive' }
    Invoke-Tool $toolArgs 'Trabalhando... jogos grandes levam alguns minutos. Não feche a janela.'
})
$scan.Add_Click({ Invoke-Tool @('scan') 'Analisando...' })
$restore.Add_Click({ Invoke-Tool @('restore') 'Restaurando...' })

Write-Log 'Escolha o arquivo e clique em "3. Aplicar".'
Write-Log 'Feche o jogo/programa antes. Nada é enviado para a internet.'
[void]$form.ShowDialog()
