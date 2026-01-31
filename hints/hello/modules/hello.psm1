function Get-HelloUser {
    $user = $env:USERNAME
    if (-not $user) {
        $user = [Environment]::UserName
    }
    "hello $user"
}

function Print-Context {
    [pscustomobject]@{
        AgentStartDir   = $env:AGENT_START_DIR
        AgentProjectDir = $env:AGENT_PROJECT_DIR
        HintModuleDir   = $env:HINT_MODULE_DIR
    }
}
