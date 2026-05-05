param(
  [ValidateSet('check', 'init', 'build')]
  [string]$Action = 'check',

  [string]$BaseUrl = '',

  [string]$ProjectDir = 'android-twa'
)

$ErrorActionPreference = 'Stop'

function Read-EnvFile {
  param([string]$Path)

  $result = @{}
  if (-not (Test-Path $Path)) {
    return $result
  }

  foreach ($line in Get-Content $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) {
      continue
    }
    $parts = $trimmed.Split('=', 2)
    if ($parts.Count -ne 2) {
      continue
    }
    $result[$parts[0].Trim()] = $parts[1].Trim()
  }

  return $result
}

function Require-Command {
  param(
    [string]$Name,
    [string]$InstallHint
  )

  $command = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -eq $command) {
    throw "$Name не найден. $InstallHint"
  }
  return $command
}

function Get-ResponseJson {
  param([string]$Url)

  $response = Invoke-WebRequest -Uri $Url -UseBasicParsing
  return $response.Content | ConvertFrom-Json
}

function Get-BaseUrl {
  param([hashtable]$EnvMap, [string]$BaseUrlOverride)

  if ($BaseUrlOverride) {
    return $BaseUrlOverride.TrimEnd('/')
  }

  if ($EnvMap.ContainsKey('PUBLIC_APP_URL') -and $EnvMap.PUBLIC_APP_URL) {
    return $EnvMap.PUBLIC_APP_URL.TrimEnd('/')
  }

  return ''
}

function Test-PublicHttpsUrl {
  param([string]$Url)

  if (-not $Url) {
    return $false
  }

  if (-not $Url.StartsWith('https://')) {
    return $false
  }

  if ($Url -match 'localhost|127\.0\.0\.1') {
    return $false
  }

  return $true
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envMap = Read-EnvFile -Path (Join-Path $repoRoot '.env')
$resolvedBaseUrl = Get-BaseUrl -EnvMap $envMap -BaseUrlOverride $BaseUrl
$manifestUrl = if ($resolvedBaseUrl) { "$resolvedBaseUrl/manifest.webmanifest" } else { '' }
$privacyUrl = if ($resolvedBaseUrl) { "$resolvedBaseUrl/privacy" } else { '' }
$assetLinksUrl = if ($resolvedBaseUrl) { "$resolvedBaseUrl/.well-known/assetlinks.json" } else { '' }

if (-not $resolvedBaseUrl) {
  throw 'Укажите PUBLIC_APP_URL в .env или передайте -BaseUrl.'
}

Write-Host "Base URL: $resolvedBaseUrl"
Write-Host "Action: $Action"

try {
  $manifest = Get-ResponseJson -Url $manifestUrl
} catch {
  throw "Не удалось загрузить manifest: $manifestUrl"
}

try {
  $privacyResponse = Invoke-WebRequest -Uri $privacyUrl -UseBasicParsing
} catch {
  throw "Не удалось открыть privacy policy: $privacyUrl"
}

try {
  $assetLinks = Get-ResponseJson -Url $assetLinksUrl
} catch {
  throw "Не удалось загрузить assetlinks: $assetLinksUrl"
}

if ($manifest.start_url -ne '/mobile') {
  throw "Manifest start_url должен быть /mobile, сейчас: $($manifest.start_url)"
}

if ($manifest.display -ne 'standalone') {
  throw "Manifest display должен быть standalone, сейчас: $($manifest.display)"
}

if (-not $manifest.icons -or $manifest.icons.Count -lt 1) {
  throw 'В manifest нет иконок приложения.'
}

if ($privacyResponse.StatusCode -ne 200) {
  throw "Privacy policy вернула статус $($privacyResponse.StatusCode)"
}

$packageName = if ($envMap.ContainsKey('ANDROID_APP_PACKAGE')) { $envMap.ANDROID_APP_PACKAGE } else { '' }
$fingerprints = if ($envMap.ContainsKey('ANDROID_SHA256_CERT_FINGERPRINTS')) { $envMap.ANDROID_SHA256_CERT_FINGERPRINTS } else { '' }

Write-Host "Manifest OK: $manifestUrl"
Write-Host "Privacy OK: $privacyUrl"
Write-Host "AssetLinks entries: $($assetLinks.Count)"
Write-Host "Android package: $packageName"

if ($Action -eq 'check') {
  if (-not (Test-PublicHttpsUrl -Url $resolvedBaseUrl)) {
    Write-Warning 'Для реальной Google Play публикации нужен публичный HTTPS URL, а не localhost/http.'
  }
  if (-not $packageName) {
    Write-Warning 'Не заполнен ANDROID_APP_PACKAGE.'
  }
  if (-not $fingerprints) {
    Write-Warning 'Не заполнен ANDROID_SHA256_CERT_FINGERPRINTS. TWA validation будет неполной до настройки signing key.'
  }
  Write-Host 'PWA/TWA readiness check завершён.'
  exit 0
}

if (-not (Test-PublicHttpsUrl -Url $resolvedBaseUrl)) {
  throw 'Для init/build нужен публичный HTTPS URL в PUBLIC_APP_URL.'
}

if (-not $packageName) {
  throw 'Заполните ANDROID_APP_PACKAGE в .env перед TWA init/build.'
}

Require-Command -Name 'npm' -InstallHint 'Установите Node.js с npm.' | Out-Null
Require-Command -Name 'bubblewrap' -InstallHint 'Установите Bubblewrap: npm install -g @bubblewrap/cli' | Out-Null

$projectPath = Join-Path $repoRoot $ProjectDir
if (-not (Test-Path $projectPath)) {
  New-Item -ItemType Directory -Path $projectPath | Out-Null
}

Push-Location $projectPath
try {
  $hasFiles = (Get-ChildItem -Force | Measure-Object).Count -gt 0

  if ($Action -eq 'init') {
    if ($hasFiles) {
      throw "Папка $ProjectDir уже не пустая. Очистите её или укажите другую через -ProjectDir."
    }

    Write-Host 'Запускаю bubblewrap init. Bubblewrap может задать вопросы про Android SDK/JDK и подпись.'
    bubblewrap init --manifest=$manifestUrl
    Write-Host "Android TWA project инициализирован в $projectPath"
  }

  if ($Action -eq 'build') {
    if (-not $hasFiles) {
      throw "Папка $ProjectDir пустая. Сначала выполните init: ./scripts/google_play_twa.ps1 -Action init"
    }

    Write-Host 'Запускаю bubblewrap build...'
    bubblewrap build
    Write-Host 'Bubblewrap build завершён. Ищите .aab/.apk в Android-проекте.'
  }
}
finally {
  Pop-Location
}