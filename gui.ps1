# FX-Reborn-F16C-Ultra 3.0 - janela simples. Aberta por "Abrir FX-Reborn-F16C-Ultra.cmd".
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'python\python.exe'
$script = Join-Path $root 'f16c_emu.py'

# ---------------------------------------------------------------------------
# Textos da interface em Português e Inglês / UI strings in Portuguese and English
# ---------------------------------------------------------------------------
$Strings = @{
    pt = @{
        title      = 'Faz um programa que exige F16C rodar em processadores sem F16C'
        step1      = '1. Escolha o executável (.exe) ou a DLL:'
        browse     = 'Procurar...'
        step2      = '2. Como salvar:'
        optReplace = 'Substituir o arquivo (o original fica guardado como backup ao lado)'
        optCopy    = 'Criar uma cópia convertida ao lado (nome_f16c.exe), sem mexer no original'
        aggressive = 'Modo agressivo (só marque se o programa ainda fechar com "instrução ilegal")'
        apply      = '3. Aplicar'
        scan       = 'Só analisar'
        restore    = 'Restaurar original'
        ready      = 'Pronto.'
        noFile     = 'Escolha um arquivo primeiro.'
        notFound   = 'Arquivo não encontrado:'
        noPython   = "Mantenha a pasta 'python' junto do programa."
        noPythonA  = 'Não achei'
        errDetails = 'Detalhes do erro:'
        done       = 'Concluído.'
        failed     = 'Terminou com erro - veja as mensagens acima.'
        dlgTitle   = 'Escolha o executável'
        dlgFilter  = 'Programas (*.exe;*.dll)|*.exe;*.dll|Todos os arquivos (*.*)|*.*'
        busyApply  = 'Trabalhando... jogos grandes levam alguns minutos. Não feche a janela.'
        busyScan   = 'Analisando...'
        busyRest   = 'Restaurando...'
        hint1      = 'Escolha o arquivo e clique em "3. Aplicar".'
        hint2      = 'Feche o jogo/programa antes. Nada é enviado para a internet.'
        language   = 'Idioma:'
    }
    en = @{
        title      = 'Makes a program that requires F16C run on CPUs without F16C'
        step1      = '1. Choose the executable (.exe) or DLL:'
        browse     = 'Browse...'
        step2      = '2. How to save:'
        optReplace = 'Replace the file (the original is kept next to it as a backup)'
        optCopy    = 'Create a converted copy next to it (name_f16c.exe), leaving the original untouched'
        aggressive = 'Aggressive mode (only if the program still closes with "illegal instruction")'
        apply      = '3. Apply'
        scan       = 'Scan only'
        restore    = 'Restore original'
        ready      = 'Ready.'
        noFile     = 'Choose a file first.'
        notFound   = 'File not found:'
        noPython   = "Keep the 'python' folder next to the program."
        noPythonA  = 'Could not find'
        errDetails = 'Error details:'
        done       = 'Done.'
        failed     = 'Finished with an error - see the messages above.'
        dlgTitle   = 'Choose the executable'
        dlgFilter  = 'Programs (*.exe;*.dll)|*.exe;*.dll|All files (*.*)|*.*'
        busyApply  = 'Working... big games take a few minutes. Do not close the window.'
        busyScan   = 'Scanning...'
        busyRest   = 'Restoring...'
        hint1      = 'Choose the file and click "3. Apply".'
        hint2      = 'Close the game/program first. Nothing is sent to the internet.'
        language   = 'Language:'
    }
}
# Idioma inicial: o do Windows (português -> pt, qualquer outro -> en)
$script:Lang = if ((Get-Culture).Name -like 'pt*') { 'pt' } else { 'en' }
function Get-UiText([string]$key) { return $Strings[$script:Lang][$key] }

$form = New-Object System.Windows.Forms.Form
$form.Text = 'FX-Reborn-F16C-Ultra 3.0'
$form.ClientSize = New-Object System.Drawing.Size(720, 520)
$form.MinimumSize = New-Object System.Drawing.Size(640, 440)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9.5)

$title = New-Object System.Windows.Forms.Label
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 11)
$title.SetBounds(14, 12, 520, 24)
$title.Anchor = 'Top,Left,Right'

$langLabel = New-Object System.Windows.Forms.Label
$langLabel.TextAlign = 'MiddleRight'
$langLabel.SetBounds(536, 13, 70, 22)
$langLabel.Anchor = 'Top,Right'

$langBox = New-Object System.Windows.Forms.ComboBox
$langBox.DropDownStyle = 'DropDownList'
[void]$langBox.Items.AddRange(@('Português', 'English'))
$langBox.SetBounds(610, 12, 96, 24)
$langBox.Anchor = 'Top,Right'

$step1 = New-Object System.Windows.Forms.Label
$step1.SetBounds(14, 48, 500, 20)

$pathBox = New-Object System.Windows.Forms.TextBox
$pathBox.SetBounds(14, 70, 580, 26)
$pathBox.Anchor = 'Top,Left,Right'
$pathBox.AllowDrop = $true

$browse = New-Object System.Windows.Forms.Button
$browse.SetBounds(602, 68, 104, 28)
$browse.Anchor = 'Top,Right'

$step2 = New-Object System.Windows.Forms.Label
$step2.SetBounds(14, 108, 500, 20)

$optReplace = New-Object System.Windows.Forms.RadioButton
$optReplace.SetBounds(28, 130, 660, 22)
$optReplace.Checked = $true

$optCopy = New-Object System.Windows.Forms.RadioButton
$optCopy.SetBounds(28, 154, 660, 22)

$aggressive = New-Object System.Windows.Forms.CheckBox
$aggressive.SetBounds(28, 182, 660, 22)

$apply = New-Object System.Windows.Forms.Button
$apply.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 10.5)
$apply.SetBounds(14, 216, 170, 36)

$scan = New-Object System.Windows.Forms.Button
$scan.SetBounds(194, 216, 130, 36)

$restore = New-Object System.Windows.Forms.Button
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
$status.SetBounds(14, 488, 692, 22)
$status.Anchor = 'Bottom,Left,Right'

$form.Controls.AddRange(@($title, $langLabel, $langBox, $step1, $pathBox, $browse, $step2, $optReplace, $optCopy,
                          $aggressive, $apply, $scan, $restore, $log, $status))

function Write-Log([string]$text) {
    $log.AppendText($text + "`r`n")
}

function Set-UiLanguage([string]$lang) {
    $script:Lang = $lang
    $title.Text      = Get-UiText 'title'
    $langLabel.Text  = Get-UiText 'language'
    $step1.Text      = Get-UiText 'step1'
    $browse.Text     = Get-UiText 'browse'
    $step2.Text      = Get-UiText 'step2'
    $optReplace.Text = Get-UiText 'optReplace'
    $optCopy.Text    = Get-UiText 'optCopy'
    $aggressive.Text = Get-UiText 'aggressive'
    $apply.Text      = Get-UiText 'apply'
    $scan.Text       = Get-UiText 'scan'
    $restore.Text    = Get-UiText 'restore'
    $status.Text     = Get-UiText 'ready'
    $log.Clear()
    Write-Log (Get-UiText 'hint1')
    Write-Log (Get-UiText 'hint2')
}

function Invoke-Tool([string[]]$toolArgs, [string]$busyText) {
    $file = $pathBox.Text.Trim().Trim('"')
    if (-not $file) {
        [System.Windows.Forms.MessageBox]::Show((Get-UiText 'noFile'), 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    if (-not (Test-Path -LiteralPath $file)) {
        [System.Windows.Forms.MessageBox]::Show("$(Get-UiText 'notFound')`n$file", 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    if (-not (Test-Path -LiteralPath $python)) {
        [System.Windows.Forms.MessageBox]::Show("$(Get-UiText 'noPythonA') $python.`n$(Get-UiText 'noPython')", 'FX-Reborn-F16C-Ultra') | Out-Null
        return
    }
    $log.Clear()
    $buttons = @($apply, $scan, $restore, $browse, $langBox)
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
    if ($err.Trim()) { Write-Log ''; Write-Log (Get-UiText 'errDetails'); Write-Log $err.Trim() }

    $form.Cursor = 'Default'
    foreach ($b in $buttons) { $b.Enabled = $true }
    if ($process.ExitCode -eq 0) { $status.Text = Get-UiText 'done' }
    else { $status.Text = Get-UiText 'failed' }
}

$browse.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = Get-UiText 'dlgTitle'
    $dialog.Filter = Get-UiText 'dlgFilter'
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
    Invoke-Tool $toolArgs (Get-UiText 'busyApply')
})
$scan.Add_Click({ Invoke-Tool @('scan') (Get-UiText 'busyScan') })
$restore.Add_Click({ Invoke-Tool @('restore') (Get-UiText 'busyRest') })

$langBox.Add_SelectedIndexChanged({
    if ($langBox.SelectedIndex -eq 0) { Set-UiLanguage 'pt' } else { Set-UiLanguage 'en' }
})

# Seleciona o idioma inicial (dispara Set-UiLanguage) / pick the initial language
$langBox.SelectedIndex = if ($script:Lang -eq 'pt') { 0 } else { 1 }
[void]$form.ShowDialog()
