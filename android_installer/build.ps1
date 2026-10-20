# Builds "Solo Web Utility v1.2.0.apk": the phone installer for Solo Web Utility.
# Output: ..\release\dist\Solo Web Utility v1.2.0.apk
# Needs: Android SDK (build-tools + platform), a JDK (javac, jar), Python 3.
$ErrorActionPreference = "Stop"

$AppDir  = $PSScriptRoot
$RootDir = Split-Path -Parent $AppDir
Set-Location $AppDir

$ApkName     = "Solo Web Utility v1.2.0.apk"
$VersionName = "1.2.0"
$VersionCode = "120"

# --- Tools -------------------------------------------------------------------------
$SdkDir = $env:ANDROID_HOME
if (-not $SdkDir) { $SdkDir = $env:ANDROID_SDK_ROOT }
if (-not $SdkDir) { $SdkDir = Join-Path $env:LOCALAPPDATA "Android\Sdk" }
if (-not (Test-Path $SdkDir)) { throw "Android SDK not found ($SdkDir). Set ANDROID_HOME." }

$BuildToolsDir = Get-ChildItem (Join-Path $SdkDir "build-tools") -Directory |
    Sort-Object { [version]($_.Name -replace '[^0-9.].*$', '') } | Select-Object -Last 1 -ExpandProperty FullName
$PlatformJar = Join-Path $SdkDir "platforms\android-34\android.jar"
if (-not (Test-Path $PlatformJar)) {
    $PlatformJar = Get-ChildItem (Join-Path $SdkDir "platforms") -Directory |
        Sort-Object { [int]($_.Name -replace '\D', '') } | Select-Object -Last 1 |
        ForEach-Object { Join-Path $_.FullName "android.jar" }
}
$Aapt2     = Join-Path $BuildToolsDir "aapt2.exe"
$D8        = Join-Path $BuildToolsDir "d8.bat"
$Zipalign  = Join-Path $BuildToolsDir "zipalign.exe"
$ApkSigner = Join-Path $BuildToolsDir "apksigner.bat"
$Javac     = (Get-Command javac.exe).Source
$JarTool   = (Get-Command jar.exe).Source
$Keytool   = (Get-Command keytool.exe).Source
$Python    = (Get-Command python.exe).Source
$JschJar   = Join-Path $AppDir "libs\jsch-0.2.20-android.jar"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Building $ApkName" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Build tools: $BuildToolsDir"
Write-Host "Platform:    $PlatformJar"

$BuildDir = Join-Path $AppDir "build_temp"
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
foreach ($d in "gen", "obj", "dex") { New-Item -ItemType Directory -Path (Join-Path $BuildDir $d) | Out-Null }
$ApkRoot = Join-Path $BuildDir "apkroot"
$AssetsDir = Join-Path $ApkRoot "assets"
$DistDir = Join-Path $RootDir "release\dist"
if (-not (Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir | Out-Null }

Write-Host "[0/7] Making sure the firmware image holds this version of Solo Web Utility..." -ForegroundColor Yellow
& $Python (Join-Path $RootDir "bake_into_firmware.py") --auto
if ($LASTEXITCODE -ne 0) {
    throw ("The firmware image could not be rebuilt with the current Solo Web Utility, so the build was stopped. " +
           "Baking needs WSL with squashfs-tools (one-time setup: 'wsl --install', then " +
           "'wsl -u root -e sh -c ""apt-get update && apt-get install -y squashfs-tools""').")
}

Write-Host "[1/7] Staging the files to install (same list as the Windows installer)..." -ForegroundColor Yellow
& $Python (Join-Path $AppDir "stage_assets.py") $AssetsDir
if ($LASTEXITCODE -ne 0) { throw "staging failed" }

Write-Host "[2/7] Compiling resources..." -ForegroundColor Yellow
& $Aapt2 compile --dir (Join-Path $AppDir "res") -o "$BuildDir\res.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Host "[3/7] Linking resources and packing the files..." -ForegroundColor Yellow
$IntermediateApk = "$BuildDir\intermediate.apk"
& $Aapt2 link "$BuildDir\res.zip" -I $PlatformJar `
    --manifest (Join-Path $AppDir "AndroidManifest.xml") `
    --min-sdk-version 24 --target-sdk-version 34 `
    --version-code $VersionCode --version-name $VersionName `
    --java "$BuildDir\gen" -o $IntermediateApk --auto-add-overlay
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }
# Assets are added with jar (always "/" paths, stored uncompressed so the phone reads them directly)
& $JarTool --update --no-compress --no-manifest --file $IntermediateApk -C $ApkRoot assets
if ($LASTEXITCODE -ne 0) { throw "adding the files to the APK failed" }

# Every staged file must be inside the APK under its exact name
$listing = & $JarTool --list --file $IntermediateApk
$inApk = @{}
foreach ($n in $listing) { $inApk[$n.Trim()] = $true }
$missing = @()
foreach ($line in Get-Content (Join-Path $AssetsDir "manifest.txt")) {
    if (-not $line.Trim()) { continue }
    $rel = ($line -split "`t")[3]
    if (-not $inApk.ContainsKey("assets/swu/$rel")) { $missing += $rel }
}
if (-not $inApk.ContainsKey("assets/manifest.txt")) { $missing += "manifest.txt" }
if ($missing.Count -gt 0) {
    Write-Host "First entries in the APK:" -ForegroundColor Red
    $listing | Select-Object -First 8 | ForEach-Object { Write-Host "  $_" }
    throw ("Files left out of the APK: " + ($missing -join ", "))
}
Write-Host ("    {0} files packed." -f @($listing | Where-Object { $_ -like "assets/swu/*" -and $_ -notlike "*/" }).Count)

Write-Host "[4/7] Compiling Java..." -ForegroundColor Yellow
$JavaSources = @(
    Get-ChildItem -Recurse "$BuildDir\gen" -Filter *.java | ForEach-Object { $_.FullName }
    Get-ChildItem -Recurse (Join-Path $AppDir "src") -Filter *.java | ForEach-Object { $_.FullName }
)
& $Javac -nowarn -encoding UTF-8 -source 8 -target 8 -cp "$PlatformJar;$JschJar" -d "$BuildDir\obj" $JavaSources
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

Write-Host "[5/7] Converting to DEX..." -ForegroundColor Yellow
$ClassFiles = Get-ChildItem -Recurse "$BuildDir\obj" -Filter *.class | ForEach-Object { $_.FullName }
& $D8 --release --min-api 24 --lib $PlatformJar --output "$BuildDir\dex" $ClassFiles $JschJar
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "[6/7] Adding code to the APK..." -ForegroundColor Yellow
Push-Location "$BuildDir\dex"
& $JarTool --update --no-manifest --file $IntermediateApk (Get-ChildItem -Filter "classes*.dex" | ForEach-Object { $_.Name })
$jarRc = $LASTEXITCODE
Pop-Location
if ($jarRc -ne 0) { throw "jar update failed" }

Write-Host "[7/7] Aligning and signing..." -ForegroundColor Yellow
$AlignedApk = "$BuildDir\aligned.apk"
& $Zipalign -p -f 4 $IntermediateApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

$KeystoreFile = Join-Path $RootDir "android_app\sololink_debug.keystore"
if (-not (Test-Path $KeystoreFile)) {
    $KeystoreFile = Join-Path $AppDir "solo_web_utility.keystore"
    if (-not (Test-Path $KeystoreFile)) {
        & $Keytool -genkeypair -validity 10000 -dname "CN=SoloLink, OU=Dev, O=3DRSolo, C=US" `
            -keystore $KeystoreFile -storepass sololink -keypass sololink -alias solokey -keyalg RSA -keysize 2048
    }
}
$FinalApk = Join-Path $DistDir $ApkName
& $ApkSigner sign --v1-signing-enabled true --v2-signing-enabled true --v4-signing-enabled false --ks $KeystoreFile `
    --ks-pass pass:sololink --key-pass pass:sololink --out $FinalApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }
& $ApkSigner verify $FinalApk
if ($LASTEXITCODE -ne 0) { throw "APK signature check failed" }

Remove-Item -Recurse -Force $BuildDir
$ApkSizeMB = (Get-Item $FinalApk).Length / 1MB
Write-Host "==========================================" -ForegroundColor Green
Write-Host ("[OK] Built {0} ({1:N1} MB)" -f $FinalApk, $ApkSizeMB) -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
