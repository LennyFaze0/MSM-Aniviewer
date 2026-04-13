$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Show-Usage {
    Write-Host "Usage:"
    Write-Host "  convert_to_t3x_windows.ps1 <input_file_or_directory> [output_directory]"
    Write-Host ""
    Write-Host "Supported input formats:"
    Write-Host "  - .png"
    Write-Host "  - .avif"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\convert_to_t3x_windows.ps1 C:\path\monster_sheet.avif"
    Write-Host "  .\convert_to_t3x_windows.ps1 C:\path\atlas_pngs C:\path\out_t3x"
    Write-Host ""
    Write-Host "Notes:"
    Write-Host "  - Requires tex3ds in PATH."
    Write-Host "  - AVIF decode tries: avifdec -> magick -> ffmpeg."
}

function Get-ToolPath([string] $Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { return $null }
    return $cmd.Source
}

function Ensure-Tool([string] $Name) {
    $path = Get-ToolPath $Name
    if ([string]::IsNullOrWhiteSpace($path)) {
        throw "Missing required command: $Name"
    }
    return $path
}

function Decode-AvifToPng([string] $InputAvif, [string] $OutputPng) {
    $avifdec = Get-ToolPath 'avifdec'
    if ($avifdec) {
        & $avifdec $InputAvif $OutputPng | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }

    $magick = Get-ToolPath 'magick'
    if ($magick) {
        & $magick $InputAvif $OutputPng | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }

    $ffmpeg = Get-ToolPath 'ffmpeg'
    if ($ffmpeg) {
        & $ffmpeg -y -loglevel error -i $InputAvif $OutputPng | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
    }

    throw "Could not decode AVIF: $InputAvif"
}

function Ensure-ParentDirectory([string] $FilePath) {
    $parent = Split-Path -Parent $FilePath
    if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Convert-One([string] $InputFile, [string] $OutputFile, [string] $Tex3dsPath) {
    $ext = [System.IO.Path]::GetExtension($InputFile).ToLowerInvariant()
    Ensure-ParentDirectory $OutputFile

    switch ($ext) {
        '.png' {
            & $Tex3dsPath -f rgba8 -z auto -o $OutputFile $InputFile | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "tex3ds failed for: $InputFile"
            }
        }
        '.avif' {
            $tmpPng = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), ([System.Guid]::NewGuid().ToString() + '.png'))
            try {
                Decode-AvifToPng $InputFile $tmpPng
                & $Tex3dsPath -f rgba8 -z auto -o $OutputFile $tmpPng | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "tex3ds failed for: $InputFile"
                }
            }
            finally {
                if (Test-Path -LiteralPath $tmpPng) {
                    Remove-Item -LiteralPath $tmpPng -Force -ErrorAction SilentlyContinue
                }
            }
        }
        default {
            Write-Host "[WARN] Skipping unsupported file: $InputFile"
        }
    }
}

try {
    if ($args.Count -lt 1 -or $args[0] -in @('-h', '--help', '/?')) {
        Show-Usage
        exit 0
    }

    $inputPath = $args[0]
    $outputDirectory = if ($args.Count -ge 2) { $args[1] } else { $null }

    if (-not (Test-Path -LiteralPath $inputPath)) {
        throw "Input path does not exist: $inputPath"
    }

    $tex3ds = Ensure-Tool 'tex3ds'

    if ([string]::IsNullOrWhiteSpace($outputDirectory)) {
        if (Test-Path -LiteralPath $inputPath -PathType Container) {
            $outputDirectory = $inputPath
        }
        else {
            $outputDirectory = Split-Path -Parent $inputPath
        }
    }

    if (-not (Test-Path -LiteralPath $outputDirectory)) {
        New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    }

    if (Test-Path -LiteralPath $inputPath -PathType Leaf) {
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($inputPath)
        $outFile = Join-Path $outputDirectory ($stem + '.t3x')
        Convert-One $inputPath $outFile $tex3ds
        Write-Host "[OK] $inputPath -> $outFile"
        exit 0
    }

    $base = [System.IO.Path]::GetFullPath($inputPath)
    $files = Get-ChildItem -LiteralPath $inputPath -Recurse -File |
        Where-Object { $_.Extension.ToLowerInvariant() -in @('.png', '.avif') }

    $count = 0
    foreach ($file in $files) {
        $rel = [System.IO.Path]::GetRelativePath($base, $file.FullName)
        $stem = [System.IO.Path]::ChangeExtension($rel, $null)
        $extNoDot = $file.Extension.TrimStart('.').ToLowerInvariant()

        $out = Join-Path $outputDirectory ($stem + '.t3x')
        if (Test-Path -LiteralPath $out) {
            $out = Join-Path $outputDirectory ($stem + '__' + $extNoDot + '.t3x')
        }

        Convert-One $file.FullName $out $tex3ds
        Write-Host "[OK] $($file.FullName) -> $out"
        $count++
    }

    Write-Host "[DONE] Converted $count file(s)."
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
