param(
    [switch]$Deploy,
    [string[]]$Only = @()
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$buildDir = "$root\infra\modules\lambda\.build"
$tmp = [System.IO.Path]::GetTempPath()

$allLambdas = @("ingest", "screen_score", "explain", "publish")
$targets = if ($Only.Count -gt 0) { $Only } else { $allLambdas }

Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

function Build-Lambda($name) {
    $srcDir = "$root\lambda_src\$name"
    $req = "$srcDir\requirements.txt"
    $zip = "$tmp\lambda_${name}.zip"

    # 依存パッケージをLinux向けにインストール
    Write-Host "[$name] 依存パッケージインストール中 (manylinux2014_x86_64)..."
    if (Test-Path $req) {
        pip install -r $req -t $srcDir --quiet --upgrade `
            --platform manylinux2014_x86_64 `
            --python-version 3.12 `
            --only-binary :all: `
            --implementation cp
    }

    # __pycache__・.pyc を除外してzipを作成（相対パス保持）
    if (Test-Path $zip) { Remove-Item $zip -Force }
    $zipStream = [System.IO.File]::Open($zip, [System.IO.FileMode]::Create)
    $archive = [System.IO.Compression.ZipArchive]::new($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)

    Get-ChildItem $srcDir -Recurse -File | Where-Object {
        ($_.FullName -notmatch '__pycache__') -and ($_.Extension -ne '.pyc')
    } | ForEach-Object {
        $entryName = $_.FullName.Substring($srcDir.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $_.FullName, $entryName) | Out-Null
    }

    $archive.Dispose()
    $zipStream.Dispose()

    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "[$name] zip作成完了: ${mb}MB"

    # S3経由でLambda更新
    $funcName = "investment-dev-" + $name.Replace("_", "-")
    $s3Key = "lambda-deploy/${name}.zip"
    $bucket = "investment-dev-config-YOUR_ACCOUNT_ID"

    aws s3 cp $zip "s3://$bucket/$s3Key" --region ap-northeast-1 | Out-Null
    $ts = aws lambda update-function-code `
        --function-name $funcName `
        --s3-bucket $bucket `
        --s3-key $s3Key `
        --region ap-northeast-1 `
        --query "LastModified" --output text
    Write-Host "[$name] Lambda更新完了: $ts"
}

foreach ($name in $targets) {
    Build-Lambda $name
}

if ($Deploy) {
    Write-Host "`nTerraform apply 実行中..."
    Set-Location "$root\infra\envs\dev"
    terraform apply -var-file="terraform.tfvars" -auto-approve
}
