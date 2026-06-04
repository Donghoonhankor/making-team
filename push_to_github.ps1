param(
  [string]$RemoteUrl = "https://github.com/Donghoonhankor/making-team.git",
  [string]$Branch = "main",
  [string]$Message = "Initial making-team workspace"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "git is not installed or not available in PATH. Install Git for Windows first."
}

if (-not (Test-Path -LiteralPath ".git")) {
  git init
}

git branch -M $Branch

$remoteExists = git remote | Where-Object { $_ -eq "origin" }
if ($remoteExists) {
  git remote set-url origin $RemoteUrl
} else {
  git remote add origin $RemoteUrl
}

git add README.md .gitignore Code.gs master_code.gs teacher_code.gs `
  math_diagram_renderer.py MathDiagramRenderer.spec 수학도표렌더러.exe `
  hwp_problem_builder.py HWPProblemBuilder.spec HWP문항생성기.exe `
  TEMPLATE_NOTES.md push_to_github.ps1

git commit -m $Message
git push -u origin $Branch

