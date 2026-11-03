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

// No PowerShell/WMI, no process killing. 0=closed, 2=still running, 3=unverifiable.
class ProcessCheck {
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
  [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref uint value, int size);
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
        using (var key = hive.OpenSubKey("Software\\" + args[1])) {
          var path = key == null ? "" : Convert.ToString(key.GetValue("InstallLocation", ""));
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
      if (args.Length != 2 || !Path.IsPathRooted(args[0]) || Path.GetFileName(args[1]) != args[1]) return 3;
      var root = Path.GetFullPath(args[0]).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
      var names = new [] { Path.GetFileNameWithoutExtension(args[1]), "hard-backend" };
      foreach (var name in names) foreach (var p in Process.GetProcessesByName(name)) {
        try { if (!p.HasExited && p.MainModule.FileName.StartsWith(root, StringComparison.OrdinalIgnoreCase)) return 2; }
        catch (InvalidOperationException) {} catch { return 3; } finally { p.Dispose(); }
      }
      return 0;
    } catch { return 3; }
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
