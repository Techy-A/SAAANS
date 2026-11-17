# Starts the SAAANS server in the background unless it is already running on port 5000.
# Run at Windows login by the SAAANS_server.vbs shortcut in the Startup folder.
$python = "C:\Users\Aryan Awasthi\AppData\Local\Programs\Python\Python311\python.exe"
$dir = "D:\CAPCIRCUITS"
$log = Join-Path $dir "start_saaans.log"

$probe = New-Object System.Net.Sockets.TcpClient
try { $running = $probe.ConnectAsync("127.0.0.1", 5000).Wait(1500) -and $probe.Connected } catch { $running = $false } finally { $probe.Close() }
if ($running) {
    return   # watchdog runs every 5 min; stay quiet when the server is up
    return
}
$env:SAANS_SIM = "0"   # never fall back to simulated data
Start-Process -FilePath $python -ArgumentList "iaq_server.py" -WorkingDirectory $dir -WindowStyle Hidden
Add-Content $log "$(Get-Date -Format s)  server started"
