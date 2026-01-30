function Get-HelloUser {
    $user = $env:USERNAME
    if (-not $user) {
        $user = [Environment]::UserName
    }
    "hello $user"
}
