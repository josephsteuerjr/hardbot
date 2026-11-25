using System;
using System.Diagnostics;
using System.IO;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;
using System.Security.Cryptography;
using Microsoft.Win32;
using System.Threading;
using System.Runtime.InteropServices;

// Windows messages request normal exit. Exit codes: 0=closed, 2=busy, 3=unverifiable.
class ProcessCheck {
  const uint QueryEndSession = 0x11, EndSession = 0x16;
  const uint AbortIfHung = 2;
  static readonly IntPtr CloseForUpdate = new IntPtr(1);
  delegate bool EnumWindow(IntPtr window, IntPtr state);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback, IntPtr state);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
  [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr window, uint message, IntPtr wparam, IntPtr lparam, uint flags, uint timeout, out IntPtr result);
  [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref uint value, int size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool CloseHandle(IntPtr handle);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder name, ref int size);
  [STAThread] static int Main(string[] args) {
    try {
      if (args.Length == 1 && (args[0] == "seal" || args[0] == "unseal")) {
        using (var input = Console.OpenStandardInput())
        using (var buffer = new MemoryStream()) {
          input.CopyTo(buffer);
          if (buffer.Length > 16 * 1024 * 1024) return 3;
          var data = args[0] == "seal"
            ? ProtectedData.Protect(buffer.ToArray(), null, DataProtectionScope.CurrentUser)
            : ProtectedData.Unprotect(buffer.ToArray(), null, DataProtectionScope.CurrentUser);
          using (var output = Console.OpenStandardOutput()) output.Write(data, 0, data.Length);
          return 0;
        }
      }
      if (args.Length == 3 && args[0] == "window") {
        var window = new IntPtr(Int64.Parse(args[1])); uint owner;
        GetWindowThreadProcessId(window, out owner);
        if (owner != UInt32.Parse(args[2])) return 3;
        uint border = 0xfffffffe, corners = 1;
        var result = DwmSetWindowAttribute(window, 34, ref border, 4);
        DwmSetWindowAttribute(window, 33, ref corners, 4); return result == 0 ? 0 : 3;
      }
      if (args.Length == 3 && args[0] == "registration") {
        using (var hive = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        using (var key = hive.OpenSubKey("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + args[1])) {
          var path = key == null ? "" : Convert.ToString(key.GetValue("InstallLocation", ""));
          if (String.IsNullOrWhiteSpace(path) && key != null) {
            var uninstall = Convert.ToString(key.GetValue("UninstallString", ""));
            var match = System.Text.RegularExpressions.Regex.Match(uninstall,
              @"^\s*(?:""(?<file>[^""]+\.exe)""|(?<file>.+?\.exe))(?:\s|$)",
              System.Text.RegularExpressions.RegexOptions.IgnoreCase);
            if (match.Success) path = Path.GetDirectoryName(match.Groups["file"].Value);
          }
          if (!Directory.Exists(path)) path = "";
          File.WriteAllText(args[2], new JavaScriptSerializer().Serialize(new { path = path }), new UTF8Encoding(false)); return 0;
        }
      }
      if (args.Length == 3 && args[0] == "cleanup") {
        var path = Path.GetFullPath(args[1]);
        var parent = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        if (!String.Equals(Path.GetDirectoryName(path), parent, StringComparison.OrdinalIgnoreCase) || !Path.GetFileName(path).StartsWith("hard-wizard-", StringComparison.Ordinal)) return 3;
        try { using (var p = Process.GetProcessById(Int32.Parse(args[2]))) p.WaitForExit(60000); } catch (ArgumentException) {}
        for (var i = 0; i < 20; i++) { try { Directory.Delete(path, true); return 0; } catch { Thread.Sleep(500); } }
        return 3;
      }
      if (args.Length == 3 && args[0] == "protect") {
        var json = new JavaScriptSerializer();
        var obj = json.Deserialize<Dictionary<string,object>>(File.ReadAllText(args[1], Encoding.UTF8));
        Protect(obj);
        object profiles;
        if (obj.TryGetValue("profiles", out profiles)) {
          var records = profiles as Dictionary<string,object>;
          if (records != null) foreach (var item in records.Values) { var p = item as Dictionary<string,object>; if (p != null) Protect(p); }
        }
        obj["token_protection"] = "windows-dpapi";
        File.WriteAllText(args[2], json.Serialize(obj), new UTF8Encoding(false)); return 0;
      }
      bool close = args.Length == 3 && args[0] == "close";
      if (!close && args.Length != 2) return 3;
      return CheckApplication(args[close ? 1 : 0], args[close ? 2 : 1], close);
    } catch (Exception error) { Console.Error.WriteLine(error.GetType().FullName + ": " + error.Message); return 3; }
  }
  static int CheckApplication(string directory, string executable, bool close) {
    if (!Path.IsPathRooted(directory) || Path.GetFileName(executable) != executable || !executable.EndsWith(".exe",StringComparison.OrdinalIgnoreCase)) return 3;
    var root = Path.GetFullPath(directory);
    var program = Path.Combine(root, executable);
    var backend = Path.Combine(root, "resources", "backend", "hard-backend.exe");
    var modern = File.Exists(Path.Combine(root, "resources", "setup-exit-v1"));
    if (close && modern)
      return Running(program).Count == 0 && Running(backend).Count == 0 ? 0 : RequestExit(program, backend);
    var deadline = Stopwatch.StartNew();
    do {
      var apps = Running(program);
      var workers = Running(backend);
      if (apps.Count == 0 && workers.Count == 0) return 0;
      if (!close) return 2;
      var windows = new List<IntPtr>();
      EnumWindows(delegate(IntPtr window, IntPtr unused) {
        uint pid;
        GetWindowThreadProcessId(window,out pid);
        if (apps.Contains(pid)) windows.Add(window);
        return true;
      },IntPtr.Zero);
      // Older releases understand ENDSESSION_CLOSEAPP. Respect any veto before
      // asking a process to exit, even when all its windows are hidden.
      var accepted = new List<IntPtr>();
      bool veto = false;
      foreach (var window in windows) {
        if (Send(window,QueryEndSession,IntPtr.Zero,CloseForUpdate)) accepted.Add(window);
        else veto = true;
      }
      foreach (var window in accepted)
        Send(window,EndSession,veto ? IntPtr.Zero : CloseForUpdate,CloseForUpdate);
      Thread.Sleep(350);
    } while (deadline.ElapsedMilliseconds < 30000);
    return Running(program).Count == 0 && Running(backend).Count == 0 ? 0 : 2;
  }
  static int RequestExit(string program, string backend) {
    var folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Hard","installer-requests");
    Directory.CreateDirectory(folder);
    string name;
    using (var hash=SHA256.Create())
      name=BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(program.ToLowerInvariant()))).Replace("-","").ToLowerInvariant();
    var file=Path.Combine(folder,name+".request");
    var owner=Guid.NewGuid().ToString("N");
    File.WriteAllText(file,owner);
    try {
      var deadline=Stopwatch.StartNew();
      while (deadline.ElapsedMilliseconds<30000) {
        if (Running(program).Count==0 && Running(backend).Count==0) return 0;
        Thread.Sleep(350);
      }
      return 2;
    } finally {
      if (File.Exists(file) && File.ReadAllText(file)==owner) File.Delete(file);
    }
  }
  static bool Send(IntPtr window,uint message,IntPtr wparam,IntPtr lparam) {
    IntPtr result;
    return SendMessageTimeout(window,message,wparam,lparam,AbortIfHung,600,out result)!=IntPtr.Zero && result!=IntPtr.Zero;
  }
  static string ImageName(Process process) {
    var handle = OpenProcess(0x1000, false, process.Id);
    if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
    try {
      var capacity = 2048;
      var path = new StringBuilder(capacity);
      if (!QueryFullProcessImageName(handle, 0, path, ref capacity)) throw new System.ComponentModel.Win32Exception();
      return path.ToString();
    } finally { CloseHandle(handle); }
  }
  static HashSet<uint> Running(string executable) {
    int session;
    using (var current = Process.GetCurrentProcess()) session=current.SessionId;
    var running=new HashSet<uint>();
    foreach (var process in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(executable))) {
      using (process) {
        try {
          if (process.SessionId==session && !process.HasExited &&
              String.Equals(ImageName(process),executable,StringComparison.OrdinalIgnoreCase))
            running.Add((uint)process.Id);
        } catch (InvalidOperationException) {} catch (System.ComponentModel.Win32Exception) {
          if (!process.HasExited) throw;
        }
      }
    }
    return running;
  }
  static void Protect(Dictionary<string,object> obj) {
    obj.Remove("key"); object v;
    if (obj.TryGetValue("device_token", out v) && v != null) {
      if (!(v is string)) throw new ArgumentException();
      var token = (string)v;
      if (token.StartsWith("dpapi-v1:")) ProtectedData.Unprotect(Convert.FromBase64String(token.Substring(9)), null, DataProtectionScope.CurrentUser);
      else if (token.Length > 0) obj["device_token"] = "dpapi-v1:" + Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(token), null, DataProtectionScope.CurrentUser));
    }
  }
}
