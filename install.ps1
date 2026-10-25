param([ValidateSet('server','client')][string]$Role, [Parameter(ValueFromRemainingArguments=$true)][string[]]$Options)
$ErrorActionPreference = 'Stop'
if (-not $Role) { $Role = Read-Host 'Install role (server / client)' }
if ($Role -notin @('server','client')) { throw 'Choose server or client.' }
& py -3 (Join-Path $PSScriptRoot 'setup.py') $Role @Options
exit $LASTEXITCODE
