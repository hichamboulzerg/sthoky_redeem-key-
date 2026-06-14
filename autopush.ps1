# Auto-commit and push any project changes to GitHub.
# Run on a schedule by the StockyAutoPush task.
$ErrorActionPreference = "SilentlyContinue"
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
$env:GIT_TERMINAL_PROMPT = "0"
Set-Location "C:\Users\Administrator.WIN-4Q9CTNFH5R7\stocky-redeem-agent"

git add -A
$changes = git status --porcelain
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if ($changes) {
    git commit -m "auto: sync $ts" | Out-Null
    $out = git push origin main 2>&1
    "[$ts] committed + pushed`n$out" | Out-File -Append -Encoding utf8 autopush.log
} else {
    "[$ts] no changes" | Out-File -Append -Encoding utf8 autopush.log
}
