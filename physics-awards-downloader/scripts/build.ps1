#requires -version 5.1
<#
.SYNOPSIS
    一键构建 Windows 单文件 exe（dist\AwardsDownloader.exe）。

.DESCRIPTION
    步骤：
      1. 定位仓库根（$PSScriptRoot 的上一级），检查 Python 版本 >= 3.9；
      2. 检查 PyInstaller 是否已安装（见 requirements-build.txt）；
      3. data\awards_index.json 不存在（或 -Reindex）时先运行 scripts\build_index.py；
      4. python -m PyInstaller packaging\AwardsDownloader.spec --noconfirm
         --distpath dist --workpath build；
      5. 打印产物路径、大小和 SHA256。

    索引是打包必需的资源：缺少索引时本脚本直接报错退出，不会静默产出
    一个「没有内置清单」的 exe。

.PARAMETER Clean
    先删除 build\、dist\ 和仓库内所有 __pycache__ 目录。

.PARAMETER Reindex
    强制重新生成数据索引（忽略已有 data\awards_index.json）。

.PARAMETER SkipIndex
    跳过索引生成。索引文件不存在时直接报错。

.PARAMETER Python
    指定 Python 解释器路径。不填时优先用仓库内 .buildenv\Scripts\python.exe，
    再退回 PATH 里的 python。

.EXAMPLE
    pwsh -File scripts\build.ps1

.EXAMPLE
    pwsh -File scripts\build.ps1 -Clean -Reindex
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Reindex,
    [switch]$SkipIndex,
    [string]$Python = "",
    [string]$DistPath = "",
    [string]$WorkPath = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Root "packaging\AwardsDownloader.spec"))) {
    throw "没有找到 packaging\AwardsDownloader.spec，脚本必须放在仓库的 scripts\ 目录下。"
}

function Resolve-PythonExe {
    param([string]$RepositoryRoot, [string]$Explicit)

    if ($Explicit) { return $Explicit }
    $venv = Join-Path $RepositoryRoot ".buildenv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    return "python"
}

$PythonExe = Resolve-PythonExe -RepositoryRoot $Root -Explicit $Python
Write-Host "==> 仓库根: $Root"
Write-Host "==> Python : $PythonExe"

# ---- 1. Python 版本 ------------------------------------------------------
$pyVersion = & $PythonExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
if ($LASTEXITCODE -ne 0) {
    throw "无法运行 Python（$PythonExe）。请安装 Python 3.9+ 的 64 位版本，或用 -Python 指定解释器。"
}
$pyVersion = $pyVersion.Trim()
$verParts = $pyVersion -split "\."
if (([int]$verParts[0] -lt 3) -or ([int]$verParts[0] -eq 3 -and [int]$verParts[1] -lt 9)) {
    throw "需要 Python 3.9 及以上，当前为 $pyVersion。"
}
Write-Host "==> Python 版本: $pyVersion"

# ---- 2. PyInstaller ------------------------------------------------------
# 注意：PowerShell 5.1 会把原生命令的 stderr 当成错误流，$ErrorActionPreference=Stop
# 时直接终止脚本，所以要 try/catch 包住探针命令，别让 "No module named PyInstaller"
# 以一个看不懂的 native error 形式冒出来。
$hasPyInstaller = $false
try {
    $null = & $PythonExe -c "import PyInstaller" 2>&1
    $hasPyInstaller = ($LASTEXITCODE -eq 0)
} catch {
    $hasPyInstaller = $false
}
if (-not $hasPyInstaller) {
    throw "当前 Python 没有安装 PyInstaller。请先运行：`"$PythonExe`" -m pip install -r requirements-build.txt"
}

# ---- 3. 清理 -------------------------------------------------------------
if ($Clean) {
    Write-Host "==> 清理 build\、dist\、__pycache__\ ..."
    foreach ($dir in @((Join-Path $Root "build"), (Join-Path $Root "dist"))) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
    Get-ChildItem -Path $Root -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Recurse -Force $_.FullName }
}

# ---- 4. 数据索引 ---------------------------------------------------------
$IndexFile = Join-Path $Root "data\awards_index.json"
if ($Reindex -and $SkipIndex) {
    throw "-Reindex 与 -SkipIndex 不能同时使用。"
}
if (-not $SkipIndex -and ($Reindex -or -not (Test-Path $IndexFile))) {
    Write-Host "==> 生成数据索引 data\awards_index.json ..."
    & $PythonExe (Join-Path $Root "scripts\build_index.py") --out $IndexFile
    if ($LASTEXITCODE -ne 0) {
        throw "build_index.py 失败（退出码 $LASTEXITCODE）。索引是打包必需资源，已中止构建。"
    }
}
if (-not (Test-Path $IndexFile)) {
    throw "缺少 data\awards_index.json。请去掉 -SkipIndex 重新运行，或先执行 `"$PythonExe`" scripts\build_index.py。"
}
$indexSizeMb = [math]::Round((Get-Item $IndexFile).Length / 1MB, 2)
Write-Host "==> 使用索引: $IndexFile ($indexSizeMb MB)"

# ---- 5. PyInstaller ------------------------------------------------------
$Spec = Join-Path $Root "packaging\AwardsDownloader.spec"
$Dist = if ($DistPath) { $DistPath } else { Join-Path $Root "dist" }
$Work = if ($WorkPath) { $WorkPath } else { Join-Path $Root "build" }
Write-Host "==> PyInstaller 打包（单文件，无控制台）..."
& $PythonExe -m PyInstaller $Spec --noconfirm --distpath $Dist --workpath $Work
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败（退出码 $LASTEXITCODE）。"
}

# ---- 6. 产物信息 ---------------------------------------------------------
$Exe = Join-Path $Dist "AwardsDownloader.exe"
if (-not (Test-Path $Exe)) {
    throw "打包完成但没有找到产物：$Exe"
}
$item = Get-Item $Exe
$sizeMb = [math]::Round($item.Length / 1MB, 2)
$sha256 = (Get-FileHash -Path $Exe -Algorithm SHA256).Hash

Write-Host ""
Write-Host "构建成功"
Write-Host "  产物  : $($item.FullName)"
Write-Host "  大小  : $sizeMb MB ($($item.Length) 字节)"
Write-Host "  SHA256: $sha256"
Write-Host ""
Write-Host "提示：程序无控制台；可用 `"$Exe`" --diag 自检，结果写入 exe 同目录的 diag.txt。"
exit 0
