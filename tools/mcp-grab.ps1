#Requires -Version 5.1
# Canonical host-side window grab for the DayZ-MCP visual pipeline (fixes the stale-frame bug,
# 2026-06-14). SINGLE source of truth: mcp_capture.py shells out to this file; spike0-window-enum.ps1
# and A6 gate-mcp.ps1 also call it. Do NOT fork the capture logic into copies.
#
# ROOT CAUSE this replaces: the old grab did SetForegroundWindow()+CopyFromScreen(rect). From a
# background PowerShell the foreground request is ignored by the Win32 foreground lock, so the window
# never came forward and CopyFromScreen read whatever was composited at those screen coordinates
# (desktop, Steam, the Cowork chat window) -> byte-identical PNGs across distinct game launches.
#
# FIX: PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2) renders the window's OWN surface (incl. the
# DWM-redirected DirectX backbuffer of a windowed client) into our DC, robust to occlusion / no focus
# / multi-monitor, without stealing focus from the user. If PrintWindow returns black (some DX paths
# do), fall back to a REAL foreground (AttachThreadInput defeats the foreground lock) + CopyFromScreen.
#
# Emits exactly one compact JSON line on stdout:
#   { ok, error, method, window:{pid,class,title,left,top,width,height}, stats:{meanBrightness,nonBlackRatio}, sha256,
#     client:{left,top,width,height}, clientStats:{meanBrightness,nonBlackRatio} }
[CmdletBinding(DefaultParameterSetName='Capture')]
param(
  [string]$ProcessName = 'DayZDiag_x64',
  [Parameter(Mandatory=$true, ParameterSetName='Capture')][string]$CapturePng,
  [Parameter(Mandatory=$true, ParameterSetName='SelfTest')][switch]$SelfTest,
  [ValidateSet('auto','printwindow','foreground','screen')][string]$Method = 'auto',
  [int]$ForegroundSettleMs = 220,
  # The default selector ("largest DayZ window of any DayZDiag pid") collides when a second DayZDiag
  # client is up (e.g. an LFQuad dev client) and grabs the WRONG window. Two ways to disambiguate:
  #   -ClientPid <id>      grab only this exact process' window. NOTE: a DayZDiag window can be owned
  #                        by a different pid than the one Start-Process returns, so prefer CmdLineMatch.
  #   -CmdLineMatch <str>  grab only the window whose owning DayZDiag process has this substring in its
  #                        command line (e.g. "-port=2402" or the unique run profiles path). This is
  #                        robust to the launcher-pid-vs-window-pid mismatch and excludes other clients
  #                        (LFQuad uses -port=2302). Resolved to the matching pid set via CIM.
  [int]$ClientPid = 0,
  [string]$CmdLineMatch = ''
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies 'System.Drawing.dll' -TypeDefinition @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class MCPGrab {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
  [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int value);
  const uint PW_RENDERFULLCONTENT = 0x00000002;
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  public class Win { public IntPtr H; public uint Pid; public string Title; public string Cls; public int Left; public int Top; public int Wd; public int Ht; public bool Vis; }

  public static void DpiAware() {
    try { SetProcessDpiAwareness(2); } catch { try { SetProcessDPIAware(); } catch {} }
  }

  public static List<Win> List() {
    var res = new List<Win>();
    EnumWindows((h,l)=>{
      uint pid; GetWindowThreadProcessId(h, out pid);
      var tb = new StringBuilder(256); GetWindowTextW(h, tb, 256);
      var cb = new StringBuilder(256); GetClassNameW(h, cb, 256);
      RECT r; GetWindowRect(h, out r);
      res.Add(new Win{ H=h, Pid=pid, Title=tb.ToString(), Cls=cb.ToString(), Left=r.Left, Top=r.Top, Wd=r.Right-r.Left, Ht=r.Bottom-r.Top, Vis=IsWindowVisible(h) });
      return true;
    }, IntPtr.Zero);
    return res;
  }

  // Live window rect (it may have moved/restored since enumeration).
  public static int[] Rect(IntPtr h) {
    RECT r; GetWindowRect(h, out r);
    return new int[]{ r.Left, r.Top, r.Right-r.Left, r.Bottom-r.Top };
  }

  public static int[] ClientRectInWindow(IntPtr h) {
    RECT outer, client;
    POINT origin = new POINT{ X=0, Y=0 };
    if (!GetWindowRect(h, out outer) || !GetClientRect(h, out client) || !ClientToScreen(h, ref origin)) return null;
    return new int[]{ origin.X-outer.Left, origin.Y-outer.Top, client.Right-client.Left, client.Bottom-client.Top };
  }

  // Grid-sampled luminance stats: {meanBrightness(0-255), nonBlackRatio(0-1)}.
  public static double[] Stats(Bitmap b) {
    if (b == null) return new double[]{ 0.0, 0.0 };
    return StatsRegion(b, 0, 0, b.Width, b.Height);
  }

  public static double[] StatsRegion(Bitmap b, int left, int top, int width, int height) {
    if (b == null || left < 0 || top < 0 || width <= 0 || height <= 0 ||
        (long)left + width > b.Width || (long)top + height > b.Height) return new double[]{ 0.0, 0.0 };
    int cols = 64, rows = 36; double sum = 0; int nb = 0, n = 0;
    for (int yi = 0; yi < rows; yi++) {
      int y = top + (int)((yi + 0.5) * height / rows); if (y >= top + height) y = top + height - 1;
      for (int xi = 0; xi < cols; xi++) {
        int x = left + (int)((xi + 0.5) * width / cols); if (x >= left + width) x = left + width - 1;
        Color p = b.GetPixel(x, y);
        double lum = 0.299 * p.R + 0.587 * p.G + 0.114 * p.B;
        sum += lum; if (lum > 9.0) nb++; n++;
      }
    }
    if (n == 0) return new double[]{ 0.0, 0.0 };
    return new double[]{ sum / n, (double)nb / n };
  }

  public static Bitmap CapturePrintWindow(IntPtr h, int w, int ht) {
    if (w <= 0 || ht <= 0) return null;
    Bitmap bmp = new Bitmap(w, ht, PixelFormat.Format32bppArgb);
    bool ok = false;
    using (Graphics g = Graphics.FromImage(bmp)) {
      IntPtr hdc = g.GetHdc();
      try { ok = PrintWindow(h, hdc, PW_RENDERFULLCONTENT); }
      finally { g.ReleaseHdc(hdc); }
    }
    if (!ok) { bmp.Dispose(); return null; }
    return bmp;
  }

  public static Bitmap CaptureScreen(int left, int top, int w, int ht) {
    if (w <= 0 || ht <= 0) return null;
    Bitmap bmp = new Bitmap(w, ht);
    using (Graphics g = Graphics.FromImage(bmp)) {
      g.CopyFromScreen(left, top, 0, 0, bmp.Size);
    }
    return bmp;
  }

  // Real foreground from a background process: attach to the current foreground thread's input queue
  // so SetForegroundWindow is honored despite the foreground lock.
  public static void ForceForeground(IntPtr h) {
    IntPtr fg = GetForegroundWindow();
    uint dummy; uint fgThread = (uint)GetWindowThreadProcessId(fg, out dummy);
    uint myThread = GetCurrentThreadId();
    bool attached = false;
    if (fgThread != 0 && fgThread != myThread) { AttachThreadInput(myThread, fgThread, true); attached = true; }
    ShowWindow(h, 9); // SW_RESTORE
    BringWindowToTop(h);
    SetForegroundWindow(h);
    if (attached) AttachThreadInput(myThread, fgThread, false);
  }
}
"@

function Emit($ok, $err, $method, $window, $stats, $sha, $client, $clientStats) {
  [pscustomobject]@{
    ok = $ok; error = $err; method = $method; window = $window; stats = $stats; sha256 = $sha
    client = $client; clientStats = $clientStats
  } | ConvertTo-Json -Compress
}

function Client-Stats($bmp, $client) {
  if (-not $bmp -or -not $client) { return [double[]]@(0.0, 0.0) }
  return [MCPGrab]::StatsRegion(
    $bmp,
    [int]$client.left,
    [int]$client.top,
    [int]$client.width,
    [int]$client.height
  )
}

function Test-LiveStats($stats) {
  return ($stats -and $stats.Count -ge 2 -and $stats[0] -gt 2.0 -and $stats[1] -gt 0.02)
}

function Is-Live($bmp, $client) {
  if (-not $bmp) { return $false }
  return Test-LiveStats (Client-Stats $bmp $client)
}

if ($SelfTest) {
  $bmp = [System.Drawing.Bitmap]::new(200, 120)
  $graphics = [System.Drawing.Graphics]::FromImage($bmp)
  $chromeBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(180, 180, 180))
  $clientBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(80, 100, 120))
  try {
    $graphics.Clear([System.Drawing.Color]::Black)
    $graphics.FillRectangle($chromeBrush, 0, 0, 200, 20)
    $client = [pscustomobject]@{ left = 0; top = 20; width = 200; height = 100 }
    $wholeStats = [MCPGrab]::Stats($bmp)
    $wholeLiveClientBlack = (Test-LiveStats $wholeStats) -and -not (Is-Live $bmp $client)

    $graphics.FillRectangle($clientBrush, 0, 20, 200, 100)
    $clientLive = Is-Live $bmp $client

    $nullStats = Client-Stats $bmp $null
    $emptyStats = [MCPGrab]::StatsRegion($bmp, 0, 20, 0, 100)
    $outsideStats = [MCPGrab]::StatsRegion($bmp, 0, 20, 201, 100)
    $cases = [ordered]@{
      whole_live_client_black = [bool]$wholeLiveClientBlack
      client_live = [bool]$clientLive
      roi_null_fail_closed = [bool]($nullStats[0] -eq 0.0 -and $nullStats[1] -eq 0.0 -and -not (Test-LiveStats $nullStats))
      roi_empty_fail_closed = [bool]($emptyStats[0] -eq 0.0 -and $emptyStats[1] -eq 0.0 -and -not (Test-LiveStats $emptyStats))
      roi_out_of_bounds_fail_closed = [bool]($outsideStats[0] -eq 0.0 -and $outsideStats[1] -eq 0.0 -and -not (Test-LiveStats $outsideStats))
      mean_boundary_strict = [bool](-not (Test-LiveStats ([double[]]@(2.0, 0.03))))
      ratio_boundary_strict = [bool](-not (Test-LiveStats ([double[]]@(3.0, 0.02))))
      above_boundaries_live = [bool](Test-LiveStats ([double[]]@(2.0001, 0.0201)))
    }
    $ok = -not ($cases.Values -contains $false)
    [pscustomobject]@{ ok = $ok; error = $(if ($ok) { '' } else { 'self_test_failed' }); cases = $cases } | ConvertTo-Json -Compress
    if (-not $ok) { exit 1 }
    exit 0
  }
  finally {
    $clientBrush.Dispose()
    $chromeBrush.Dispose()
    $graphics.Dispose()
    $bmp.Dispose()
  }
}

[MCPGrab]::DpiAware()

$wins  = [MCPGrab]::List()
$shown = $wins | Where-Object { $_.Vis -and $_.Wd -gt 0 -and $_.Ht -gt 0 -and $_.Cls -eq 'DayZ' -and $_.Cls -ne 'ConsoleWindowClass' }

# Build the candidate window set in priority order; each selector narrows to the target client and
# excludes other DayZ clients (e.g. LFQuad). Fall through to the next only if a selector yields no
# DayZ render window, so an unreliable signal (CmdLineMatch when the window pid lacks the args, or a
# launcher pid that does not own the window) degrades gracefully instead of hard-failing.
function Pids-ForCmdline($needle) {
  @(Get-CimInstance Win32_Process -Filter "Name='$ProcessName.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) } |
    ForEach-Object { [uint32]$_.ProcessId })
}
function Pick($pidset) {
  if (-not $pidset -or $pidset.Count -eq 0) { return $null }
  return ($shown | Where-Object { $pidset -contains $_.Pid } | Sort-Object @{Expression={$_.Wd * $_.Ht}} -Descending | Select-Object -First 1)
}

$chosen = $null
$selVia = ''
$selErr = 'window_not_found'
if ($CmdLineMatch -ne '') {
  $selErr = "window_not_found_for_cmdline_$($CmdLineMatch -replace '[^A-Za-z0-9]','_')"
  $chosen = Pick (Pids-ForCmdline $CmdLineMatch)
  if ($chosen) { $selVia = 'cmdline' }
}
if (-not $chosen -and $ClientPid -gt 0) {
  if ($selErr -eq 'window_not_found') { $selErr = "window_not_found_for_pid_$ClientPid" }
  $chosen = Pick @([uint32]$ClientPid)
  if ($chosen) { $selVia = 'pid' }
}
if (-not $chosen -and $CmdLineMatch -eq '' -and $ClientPid -le 0) {
  # No disambiguation requested: legacy behaviour, largest DayZ window of any DayZDiag pid.
  $procs = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
  $chosen = Pick @($procs | ForEach-Object { [uint32]$_.Id })
  if ($chosen) { $selVia = 'any' }
}

if (-not $chosen) {
  Emit $false $selErr '' $null $null '' $null $null
  exit 0
}

function Get-CaptureGeometry() {
  $rect = [MCPGrab]::Rect($chosen.H)
  $left = $rect[0]; $top = $rect[1]; $width = $rect[2]; $height = $rect[3]
  if ($width -le 0) { $width = $chosen.Wd }
  if ($height -le 0) { $height = $chosen.Ht }
  $clientRect = [MCPGrab]::ClientRectInWindow($chosen.H)
  $client = $null
  if ($clientRect -and $clientRect.Count -eq 4) {
    $client = [pscustomobject]@{
      left = $clientRect[0]; top = $clientRect[1]; width = $clientRect[2]; height = $clientRect[3]
    }
  }
  return [pscustomobject]@{
    left = $left; top = $top; width = $width; height = $height
    window = [pscustomobject]@{
      pid = $chosen.Pid; class = $chosen.Cls; title = $chosen.Title
      left = $left; top = $top; width = $width; height = $height
    }
    client = $client
  }
}

function Save-And-Emit($bmp, $method, $geometry) {
  $st = [MCPGrab]::Stats($bmp)
  $clientSt = Client-Stats $bmp $geometry.client
  $bmp.Save($CapturePng, [System.Drawing.Imaging.ImageFormat]::Png)
  $bmp.Dispose()
  $sha = (Get-FileHash -LiteralPath $CapturePng -Algorithm SHA256).Hash
  $stats = [pscustomobject]@{ meanBrightness = $st[0]; nonBlackRatio = $st[1] }
  $clientStats = [pscustomobject]@{ meanBrightness = $clientSt[0]; nonBlackRatio = $clientSt[1] }
  Emit $true '' $method $geometry.window $stats $sha $geometry.client $clientStats
}

# auto: PrintWindow first (occlusion-robust, no focus theft); fall back to real-foreground grab.
if ($Method -eq 'printwindow' -or $Method -eq 'auto') {
  $geometry = Get-CaptureGeometry
  $bmp = [MCPGrab]::CapturePrintWindow($chosen.H, $geometry.width, $geometry.height)
  if ($Method -eq 'printwindow') {
    if ($bmp) { Save-And-Emit $bmp 'printwindow' $geometry; exit 0 } else { Emit $false 'printwindow_failed' 'printwindow' $geometry.window $null '' $geometry.client $null; exit 0 }
  }
  if (Is-Live $bmp $geometry.client) { Save-And-Emit $bmp 'printwindow' $geometry; exit 0 }
  if ($bmp) { $bmp.Dispose() }
}

if ($Method -eq 'foreground' -or $Method -eq 'auto') {
  [MCPGrab]::ForceForeground($chosen.H)
  Start-Sleep -Milliseconds $ForegroundSettleMs
  $geometry = Get-CaptureGeometry
  $bmp = [MCPGrab]::CaptureScreen($geometry.left, $geometry.top, $geometry.width, $geometry.height)
  if ($Method -eq 'foreground') {
    if ($bmp) { Save-And-Emit $bmp 'foreground' $geometry; exit 0 } else { Emit $false 'foreground_failed' 'foreground' $geometry.window $null '' $geometry.client $null; exit 0 }
  }
  if (Is-Live $bmp $geometry.client) { Save-And-Emit $bmp 'foreground' $geometry; exit 0 }
  if ($bmp) { $bmp.Dispose() }
}

# screen: plain CopyFromScreen (baseline / last resort).
$geometry = Get-CaptureGeometry
$bmp = [MCPGrab]::CaptureScreen($geometry.left, $geometry.top, $geometry.width, $geometry.height)
if ($bmp) { Save-And-Emit $bmp 'screen' $geometry; exit 0 }
Emit $false 'all_methods_failed' '' $geometry.window $null '' $geometry.client $null
exit 0
