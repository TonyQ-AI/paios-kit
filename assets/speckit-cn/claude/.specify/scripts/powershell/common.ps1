#!/usr/bin/env pwsh
# Common PowerShell functions analogous to common.sh

function Get-RepoRoot {
    try {
        $result = git rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $result
        }
    } catch {
        # Git command failed
    }
    
    # Fall back to script location for non-git repos
    return (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
}

function Get-CurrentBranch {
    # Slice identity is NOT derived from the git branch: projects using this
    # toolchain work directly on the default branch. Resolution order:
    #   1. SPECIFY_FEATURE environment variable (explicit override, set by
    #      create-new-feature.ps1)
    #   2. .specify/feature.json "feature_directory" (persisted by
    #      create-new-feature.ps1)
    #   3. current git branch, only when it already looks like a numbered slice
    #      (legacy repositories that still carry feature branches)
    #   4. highest numbered directory under specs/ (lookup fallback)
    #   5. "main"
    if ($env:SPECIFY_FEATURE) {
        return $env:SPECIFY_FEATURE
    }

    $repoRoot = Get-RepoRoot
    $featureJson = Join-Path $repoRoot ".specify/feature.json"
    if (Test-Path $featureJson) {
        try {
            $cfg = (Get-Content -LiteralPath $featureJson -Raw) | ConvertFrom-Json
            if ($cfg.feature_directory) {
                return (Split-Path -Leaf ($cfg.feature_directory.TrimEnd('/', '\')))
            }
        } catch {
            # State file unreadable: fall through to the remaining sources
        }
    }

    try {
        $result = git rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $result -match '^[0-9]{3}-') {
            return $result
        }
    } catch {
        # Git command failed
    }

    $specsDir = Join-Path $repoRoot "specs"

    if (Test-Path $specsDir) {
        $latestFeature = ""
        $highest = 0

        Get-ChildItem -Path $specsDir -Directory | ForEach-Object {
            if ($_.Name -match '^(\d{3})-') {
                $num = [int]$matches[1]
                if ($num -gt $highest) {
                    $highest = $num
                    $latestFeature = $_.Name
                }
            }
        }

        if ($latestFeature) {
            return $latestFeature
        }
    }

    # Final fallback
    return "main"
}

function Test-HasGit {
    try {
        git rev-parse --show-toplevel 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Test-FeatureBranch {
    param(
        [string]$Branch,
        [bool]$HasGit = $true
    )

    # Branch-agnostic validation: the slice identity no longer comes from a git
    # branch (projects work directly on the default branch), but it must still
    # resolve to a numbered slice such as 001-data-layer via SPECIFY_FEATURE,
    # .specify/feature.json or specs/.
    if (-not $HasGit) {
        Write-Warning "[specify] Warning: Git repository not detected; skipped branch validation"
        return $true
    }

    if ($Branch -notmatch '^[0-9]{3}-') {
        # Written to stderr on purpose: Write-Output here would be collected into
        # this function's return value and make the callers' "-not" check fail.
        [Console]::Error.WriteLine("ERROR: No slice context resolved. Current feature: $Branch")
        [Console]::Error.WriteLine("Expected a numbered slice directory such as: 001-feature-name")
        [Console]::Error.WriteLine("Run specify first to create specs/<NNN-name>/ and .specify/feature.json.")
        return $false
    }
    return $true
}

function Get-FeatureDir {
    param([string]$RepoRoot, [string]$Branch)
    Join-Path $RepoRoot "specs/$Branch"
}

# Persist the current slice to .specify/feature.json. Written only when the
# stored value differs, so repeated calls do not dirty the working tree.
function Save-FeatureJson {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$FeatureDirectory
    )

    # Store the path relative to the repository root when possible.
    $prefix = $RepoRoot + [System.IO.Path]::DirectorySeparatorChar
    if ($null -ne $IsWindows) { $onWin = $IsWindows } else { $onWin = $true }
    if ($onWin) {
        $cmp = [System.StringComparison]::OrdinalIgnoreCase
    } else {
        $cmp = [System.StringComparison]::Ordinal
    }
    if ($FeatureDirectory.StartsWith($prefix, $cmp)) {
        $FeatureDirectory = $FeatureDirectory.Substring($prefix.Length)
    }

    $fjPath = Join-Path (Join-Path $RepoRoot '.specify') 'feature.json'

    if (Test-Path -LiteralPath $fjPath -PathType Leaf) {
        try {
            $raw = Get-Content -LiteralPath $fjPath -Raw
            $cfg = $raw | ConvertFrom-Json
            if ($cfg.feature_directory -eq $FeatureDirectory) {
                return
            }
        } catch {
            # File is corrupt or unreadable: overwrite it
        }
    }

    $specifyDir = Join-Path $RepoRoot '.specify'
    if (-not (Test-Path -LiteralPath $specifyDir -PathType Container)) {
        New-Item -ItemType Directory -Path $specifyDir -Force | Out-Null
    }

    $json = @{ feature_directory = $FeatureDirectory } | ConvertTo-Json -Compress
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($fjPath, $json, $utf8NoBom)
}

function Get-FeaturePathsEnv {
    $repoRoot = Get-RepoRoot
    $currentBranch = Get-CurrentBranch
    $hasGit = Test-HasGit

    # Resolve the slice directory. Priority:
    #   1. SPECIFY_FEATURE_DIRECTORY environment variable (explicit override)
    #   2. .specify/feature.json "feature_directory"
    #   3. specs/<CURRENT_BRANCH>
    $featureDir = $null
    if ($env:SPECIFY_FEATURE_DIRECTORY) {
        $featureDir = $env:SPECIFY_FEATURE_DIRECTORY
        if (-not [System.IO.Path]::IsPathRooted($featureDir)) {
            $featureDir = Join-Path $repoRoot $featureDir
        }
        Save-FeatureJson -RepoRoot $repoRoot -FeatureDirectory $env:SPECIFY_FEATURE_DIRECTORY
    } else {
        $featureJson = Join-Path $repoRoot ".specify/feature.json"
        if (Test-Path $featureJson) {
            try {
                $cfg = (Get-Content -LiteralPath $featureJson -Raw) | ConvertFrom-Json
                if ($cfg.feature_directory) {
                    $featureDir = $cfg.feature_directory
                    if (-not [System.IO.Path]::IsPathRooted($featureDir)) {
                        $featureDir = Join-Path $repoRoot $featureDir
                    }
                }
            } catch {
                Write-Warning "[specify] Warning: .specify/feature.json is unreadable; falling back to specs/$currentBranch"
            }
        }
        if (-not $featureDir) {
            $featureDir = Get-FeatureDir -RepoRoot $repoRoot -Branch $currentBranch
        }
    }
    
    [PSCustomObject]@{
        REPO_ROOT     = $repoRoot
        CURRENT_BRANCH = $currentBranch
        HAS_GIT       = $hasGit
        FEATURE_DIR   = $featureDir
        FEATURE_SPEC  = Join-Path $featureDir 'spec.md'
        IMPL_PLAN     = Join-Path $featureDir 'plan.md'
        TASKS         = Join-Path $featureDir 'tasks.md'
        RESEARCH      = Join-Path $featureDir 'research.md'
        DATA_MODEL    = Join-Path $featureDir 'data-model.md'
        QUICKSTART    = Join-Path $featureDir 'quickstart.md'
        CONTRACTS_DIR = Join-Path $featureDir 'contracts'
    }
}

function Test-FileExists {
    param([string]$Path, [string]$Description)
    if (Test-Path -Path $Path -PathType Leaf) {
        Write-Output "  ✓ $Description"
        return $true
    } else {
        Write-Output "  ✗ $Description"
        return $false
    }
}

function Test-DirHasFiles {
    param([string]$Path, [string]$Description)
    if ((Test-Path -Path $Path -PathType Container) -and (Get-ChildItem -Path $Path -ErrorAction SilentlyContinue | Where-Object { -not $_.PSIsContainer } | Select-Object -First 1)) {
        Write-Output "  ✓ $Description"
        return $true
    } else {
        Write-Output "  ✗ $Description"
        return $false
    }
}
