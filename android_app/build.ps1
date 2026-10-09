# SoloLink Viewer APK Build Script
$ErrorActionPreference = "Stop"

$AppDir = $PSScriptRoot
Set-Location $AppDir

$SdkDir = "C:\Users\User\AppData\Local\Android\Sdk"
$BuildToolsVersion = "34.0.0"
$BuildToolsDir = Join-Path $SdkDir "build-tools\$BuildToolsVersion"
$PlatformJar = Join-Path $SdkDir "platforms\android-34\android.jar"

$Aapt2 = Join-Path $BuildToolsDir "aapt2.exe"
$D8 = Join-Path $BuildToolsDir "d8.bat"
$Zipalign = Join-Path $BuildToolsDir "zipalign.exe"
$ApkSigner = Join-Path $BuildToolsDir "apksigner.bat"
$JarTool = (Get-Command jar.exe).Source
$Keytool = (Get-Command keytool.exe).Source
$Javac = (Get-Command javac.exe).Source

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Building Full-Package SoloLink Viewer APK" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# Prepare build directories
$BuildDir = Join-Path $AppDir "build_temp"
if (Test-Path $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
New-Item -ItemType Directory -Path "$BuildDir\gen" | Out-Null
New-Item -ItemType Directory -Path "$BuildDir\obj" | Out-Null
New-Item -ItemType Directory -Path "$BuildDir\dex" | Out-Null
New-Item -ItemType Directory -Path "$BuildDir\res_compiled" | Out-Null

$DistDir = Join-Path $AppDir "dist"
if (!(Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir | Out-Null
}

Write-Host "[1/6] Compiling resources with aapt2..." -ForegroundColor Yellow
& $Aapt2 compile --dir "$AppDir\res" -o "$BuildDir\res_compiled.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Host "[2/6] Linking resources and assets with aapt2..." -ForegroundColor Yellow
$IntermediateApk = "$BuildDir\intermediate.apk"
$AssetsDir = Join-Path $AppDir "assets"

if (Test-Path $AssetsDir) {
    & $Aapt2 link "$BuildDir\res_compiled.zip" `
        -I $PlatformJar `
        --manifest "$AppDir\AndroidManifest.xml" `
        --min-sdk-version 24 `
        --target-sdk-version 34 `
        --version-code 1 `
        --version-name "1.0" `
        -A $AssetsDir `
        -0 gz -0 tar.gz -0 apj -0 pdf `
        --java "$BuildDir\gen" `
        -o $IntermediateApk `
        --auto-add-overlay
} else {
    & $Aapt2 link "$BuildDir\res_compiled.zip" `
        -I $PlatformJar `
        --manifest "$AppDir\AndroidManifest.xml" `
        --min-sdk-version 24 `
        --target-sdk-version 34 `
        --version-code 1 `
        --version-name "1.0" `
        --java "$BuildDir\gen" `
        -o $IntermediateApk `
        --auto-add-overlay
}
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

Write-Host "[3/6] Compiling Java sources with javac..." -ForegroundColor Yellow
$JavaSources = @(
    Get-ChildItem -Recurse "$BuildDir\gen" -Filter *.java | ForEach-Object { $_.FullName }
    Get-ChildItem -Recurse "$AppDir\src" -Filter *.java | ForEach-Object { $_.FullName }
)
& $Javac -encoding UTF-8 -cp $PlatformJar -d "$BuildDir\obj" $JavaSources
if ($LASTEXITCODE -ne 0) { throw "javac compilation failed" }

Write-Host "[4/6] Converting bytecode to DEX with d8..." -ForegroundColor Yellow
$ClassFiles = Get-ChildItem -Recurse "$BuildDir\obj" -Filter *.class | ForEach-Object { $_.FullName }
& $D8 --min-api 24 --output "$BuildDir\dex" $ClassFiles
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "[5/6] Packaging classes.dex into APK..." -ForegroundColor Yellow
Push-Location "$BuildDir\dex"
& $JarTool -uf $IntermediateApk classes.dex
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "jar packaging failed" }

$AlignedApk = "$BuildDir\aligned.apk"
Write-Host "[6/6] Zipaligning and signing APK..." -ForegroundColor Yellow
& $Zipalign -p -f 4 $IntermediateApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }

# Keystore check / generation
$KeystoreFile = Join-Path $AppDir "sololink_debug.keystore"
if (!(Test-Path $KeystoreFile)) {
    Write-Host "Generating debug keystore..." -ForegroundColor Cyan
    & $Keytool -genkeypair -validity 10000 `
        -dname "CN=SoloLink, OU=Dev, O=3DRSolo, C=US" `
        -keystore $KeystoreFile `
        -storepass sololink `
        -keypass sololink `
        -alias solokey `
        -keyalg RSA -keysize 2048
}

$FinalApk = Join-Path $DistDir "Solo Web Utility Installer.apk"
& $ApkSigner sign --v1-signing-enabled true --v2-signing-enabled true --ks $KeystoreFile --ks-pass pass:sololink --key-pass pass:sololink --out $FinalApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }

# Copy to root directory, docs, and dist
$RootDir = Split-Path -Parent $AppDir
Copy-Item $FinalApk (Join-Path $RootDir "Solo Web Utility Installer.apk") -Force
if (Test-Path (Join-Path $RootDir "docs")) {
    Copy-Item $FinalApk (Join-Path (Join-Path $RootDir "docs") "Solo Web Utility Installer.apk") -Force
}
if (Test-Path (Join-Path $RootDir "dist")) {
    Copy-Item $FinalApk (Join-Path (Join-Path $RootDir "dist") "Solo Web Utility Installer.apk") -Force
}
if (Test-Path (Join-Path $RootDir "static")) {
    Copy-Item $FinalApk (Join-Path (Join-Path $RootDir "static") "Solo Web Utility Installer.apk") -Force
}

Write-Host "Verifying APK..." -ForegroundColor Cyan
& $ApkSigner verify --verbose $FinalApk

$ApkSizeMB = (Get-Item $FinalApk).Length / 1MB
Write-Host "==========================================" -ForegroundColor Green
Write-Host ("SUCCESS: Full APK generated at {0} ({1:N2} MB)" -f $FinalApk, $ApkSizeMB) -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
