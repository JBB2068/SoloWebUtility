# SoloLink Viewer APK build (the small phone app, NOT the Solo Web Utility Installer).
# Output: android_app\dist\sololink_viewer.apk, copied to the project root (sololink_viewer.apk) and to
# static\sololink_viewer.apk.bin, which the Solo serves at http://10.1.1.10/sololink_viewer.apk.
# static\ is baked into the firmware image: rebake after running this.
# Same key as every earlier Viewer build (sololink_debug.keystore), so it installs over the old app.
# Do NOT use build.ps1 here: that older script packs assets\ and overwrites the Installer APK.
$ErrorActionPreference = "Stop"

$VersionCode = 6
$VersionName = "1.6"

$AppDir = $PSScriptRoot
Set-Location $AppDir
$RootDir = Split-Path -Parent $AppDir

$SdkDir = $env:ANDROID_HOME
if (-not $SdkDir) { $SdkDir = $env:ANDROID_SDK_ROOT }
if (-not $SdkDir) { $SdkDir = Join-Path $env:LOCALAPPDATA "Android\Sdk" }
$BuildToolsDir = Join-Path $SdkDir "build-tools\34.0.0"
if (!(Test-Path $BuildToolsDir)) {
    $BuildToolsDir = (Get-ChildItem (Join-Path $SdkDir "build-tools") | Sort-Object Name -Descending | Select-Object -First 1).FullName
}
$PlatformJar = Join-Path $SdkDir "platforms\android-34\android.jar"
if (!(Test-Path $PlatformJar)) { throw "Android platform 34 (android.jar) not found in $SdkDir" }

$Aapt2 = Join-Path $BuildToolsDir "aapt2.exe"
$D8 = Join-Path $BuildToolsDir "d8.bat"
$Zipalign = Join-Path $BuildToolsDir "zipalign.exe"
$ApkSigner = Join-Path $BuildToolsDir "apksigner.bat"
$JarTool = (Get-Command jar.exe).Source
$Javac = (Get-Command javac.exe).Source

Write-Host "Building SoloLink Viewer $VersionName ($VersionCode)" -ForegroundColor Cyan

$BuildDir = Join-Path $AppDir "build_viewer_temp"
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Path "$BuildDir\gen", "$BuildDir\obj", "$BuildDir\dex" | Out-Null
$DistDir = Join-Path $AppDir "dist"
if (!(Test-Path $DistDir)) { New-Item -ItemType Directory -Path $DistDir | Out-Null }

Write-Host "[1/6] aapt2 compile" -ForegroundColor Yellow
& $Aapt2 compile --dir "$AppDir\res" -o "$BuildDir\res_compiled.zip"
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile failed" }

Write-Host "[2/6] aapt2 link (no assets)" -ForegroundColor Yellow
$IntermediateApk = "$BuildDir\intermediate.apk"
& $Aapt2 link "$BuildDir\res_compiled.zip" `
    -I $PlatformJar `
    --manifest "$AppDir\AndroidManifest.xml" `
    --min-sdk-version 24 `
    --target-sdk-version 34 `
    --version-code $VersionCode `
    --version-name $VersionName `
    --java "$BuildDir\gen" `
    -o $IntermediateApk `
    --auto-add-overlay
if ($LASTEXITCODE -ne 0) { throw "aapt2 link failed" }

Write-Host "[3/6] javac" -ForegroundColor Yellow
$JavaSources = @(
    Get-ChildItem -Recurse "$BuildDir\gen" -Filter *.java | ForEach-Object { $_.FullName }
    Get-ChildItem -Recurse "$AppDir\src" -Filter *.java | ForEach-Object { $_.FullName }
)
& $Javac -encoding UTF-8 --release 8 -cp $PlatformJar -d "$BuildDir\obj" $JavaSources
if ($LASTEXITCODE -ne 0) {
    # older JDKs have no --release with a bootclasspath jar: plain source/target
    & $Javac -encoding UTF-8 -source 8 -target 8 -cp $PlatformJar -d "$BuildDir\obj" $JavaSources
    if ($LASTEXITCODE -ne 0) { throw "javac failed" }
}

Write-Host "[4/6] d8" -ForegroundColor Yellow
$ClassFiles = Get-ChildItem -Recurse "$BuildDir\obj" -Filter *.class | ForEach-Object { $_.FullName }
& $D8 --min-api 24 --lib $PlatformJar --output "$BuildDir\dex" $ClassFiles
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Write-Host "[5/6] add classes.dex" -ForegroundColor Yellow
Push-Location "$BuildDir\dex"
& $JarTool -uf $IntermediateApk classes.dex
$jarExit = $LASTEXITCODE
Pop-Location
if ($jarExit -ne 0) { throw "jar packaging failed" }

Write-Host "[6/6] zipalign + sign" -ForegroundColor Yellow
$AlignedApk = "$BuildDir\aligned.apk"
& $Zipalign -p -f 4 $IntermediateApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "zipalign failed" }
$KeystoreFile = Join-Path $AppDir "sololink_debug.keystore"
if (!(Test-Path $KeystoreFile)) { throw "sololink_debug.keystore is missing: a new key would not install over the old app" }
$FinalApk = Join-Path $DistDir "sololink_viewer.apk"
& $ApkSigner sign --v1-signing-enabled true --v2-signing-enabled true --ks $KeystoreFile --ks-key-alias solokey `
    --ks-pass pass:sololink --key-pass pass:sololink --out $FinalApk $AlignedApk
if ($LASTEXITCODE -ne 0) { throw "apksigner failed" }
& $ApkSigner verify $FinalApk
if ($LASTEXITCODE -ne 0) { throw "apksigner verify failed" }

Copy-Item $FinalApk (Join-Path $RootDir "sololink_viewer.apk") -Force
Copy-Item $FinalApk (Join-Path $RootDir "static\sololink_viewer.apk.bin") -Force
Remove-Item -Recurse -Force $BuildDir

$Size = (Get-Item $FinalApk).Length / 1KB
Write-Host ("SUCCESS: SoloLink Viewer {0} ({1:N0} KB) -> sololink_viewer.apk and static\sololink_viewer.apk.bin" -f $VersionName, $Size) -ForegroundColor Green
Write-Host "Next: rebake the firmware image (static\ changed)." -ForegroundColor Green
