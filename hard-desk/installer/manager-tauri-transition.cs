using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal static class ManagerTauriTransition {
    private const string ProductId = "ru.rimborso.hard.manager";
    private const string InstallKey = @"Software\44c820db-a275-56a9-b014-8787cc7285cb";
    private const string Primary = "https://hard.rimborso.ru";
    private const string ProgramName = "hard-manager.exe";
    private static readonly Encoding Utf8 = new UTF8Encoding(false, true);
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_open_v2(byte[] path, out IntPtr db, int flags, IntPtr vfs);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_close(IntPtr db);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_prepare_v2(IntPtr db, byte[] sql, int length, out IntPtr statement, IntPtr tail);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_step(IntPtr statement);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_finalize(IntPtr statement);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern IntPtr sqlite3_column_blob(IntPtr statement, int index);
    [DllImport("winsqlite3.dll", CallingConvention = CallingConvention.Cdecl)]
    private static extern int sqlite3_column_bytes(IntPtr statement, int index);
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref uint value, int size);

    [STAThread]
    private static int Main() {
        using (var mutex = new Mutex(false, @"Local\Rimborso.Manager.TauriTransition")) {
            try { if (!mutex.WaitOne(0)) return 2; }
            catch (AbandonedMutexException) { }
            try { return RunWindow(); }
            finally { mutex.ReleaseMutex(); }
        }
    }

    private static int RunWindow() {
        Application.EnableVisualStyles();
        var status = new Label {
            Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Segoe UI", 12), Text = "Готовим обновление Руководителя…"
        };
        using (var form = new Form {
            ClientSize = new Size(490, 112), StartPosition = FormStartPosition.CenterScreen,
            BackColor = Color.FromArgb(248, 238, 224), ForeColor = Color.FromArgb(46, 34, 27),
            FormBorderStyle = FormBorderStyle.None, Text = "Обновление Руководителя"
        }) {
            int result = 2;
            bool done = false;
            form.Controls.Add(status);
            form.FormClosing += (sender, args) => { args.Cancel = !done; };
            form.HandleCreated += (sender, args) => {
                uint noBorder = 0xfffffffe, square = 1;
                DwmSetWindowAttribute(form.Handle, 34, ref noBorder, 4);
                DwmSetWindowAttribute(form.Handle, 33, ref square, 4);
            };
            form.Shown += (sender, args) => {
                var worker = new Thread(() => {
                    try {
                        Run(message => form.BeginInvoke((Action)(() => status.Text = message)));
                        result = 0;
                    } catch (Exception error) {
                        form.Invoke((Action)(() => MessageBox.Show(form, error is TransitionError ? error.Message :
                            "Не удалось завершить обновление. Запустите прежний ярлык и попробуйте ещё раз.",
                            form.Text, MessageBoxButtons.OK, MessageBoxIcon.Warning)));
                    } finally {
                        form.BeginInvoke((Action)(() => { done = true; form.Close(); }));
                    }
                });
                worker.IsBackground = true;
                worker.Start();
            };
            Application.Run(form);
            return result;
        }
    }

    private static void Run(Action<string> progress) {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string profile = Path.Combine(local, "Hard", "manager", "data");
        string native = Path.Combine(local, ProductId, "manager", "desktop.sqlite");
        string install;
        using (var user = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        using (var registration = user.OpenSubKey(InstallKey))
            install = registration == null ? "" : Convert.ToString(registration.GetValue("InstallLocation", ""));
        if (!Path.IsPathRooted(install) || !File.Exists(native))
            throw new TransitionError("Не найдена прежняя установка Руководителя.");
        install = Path.GetFullPath(install).TrimEnd(Path.DirectorySeparatorChar);
        CheckPath(install);
        CheckPath(native);
        CheckPath(profile);
        string program = Path.Combine(install, ProgramName);
        CheckPath(program);
        var manifest = ReadJson(Path.Combine(install, "runtime.json"));
        if (Value(manifest, "schema") != "rimborso.native.manager.v1" ||
            Value(manifest, "identifier") != ProductId || Value(manifest, "version") != "3.5.0" ||
            !File.Exists(program))
            throw new TransitionError("Этот переход предназначен для установленного Руководителя Tauri 3.5.0.");

        string work = Path.Combine(local, "Hard", "manager", "upgrade-recovery", "tauri-transition-" + Guid.NewGuid().ToString("N"));
        CheckPath(work);
        Directory.CreateDirectory(work);
        string payload = Path.Combine(work, "manager-setup.exe");
        progress("Загружаем подписанный пакет CRM…");
        try {
            Download(payload);
            WaitForManagerExit(program);
            var session = ReadNativeSession(native);
            ValidateSession(session);
            string backup = Path.Combine(work, "previous-program");
            CopyDirectory(install, backup);
            WriteAtomic(Path.Combine(work, "install-location.txt"), Utf8.GetBytes(install));
            progress("Устанавливаем обновление Руководителя…");
            Install(payload, install, profile, session);
            Process.Start(new ProcessStartInfo(program) { WorkingDirectory = install, UseShellExecute = false });
        } finally {
            if (File.Exists(payload)) File.Delete(payload);
        }
    }

    private static void WaitForManagerExit(string program) {
        int ownSession;
        using (var current = Process.GetCurrentProcess()) ownSession = current.SessionId;
        for (var attempt = 0; attempt < 25; attempt++) {
            bool running = false;
            foreach (var process in Process.GetProcessesByName("hard-manager")) {
                using (process) {
                    try { running |= process.SessionId == ownSession &&
                        String.Equals(process.MainModule.FileName, program, StringComparison.OrdinalIgnoreCase); }
                    catch (InvalidOperationException) { }
                }
            }
            if (!running) return;
            Thread.Sleep(400);
        }
        throw new TransitionError("Закройте Руководителя через значок у часов и повторите обновление.");
    }

    private static void Install(string payload, string install, string profile, Dictionary<string, object> nativeSession) {
        // The stable signed NSIS package owns its current-user registration and shortcuts.
        // The previous binary tree is retained separately if that installer needs recovery.
        using (var child = Process.Start(new ProcessStartInfo(payload, "/S /currentuser --updated") {
            UseShellExecute = false, CreateNoWindow = true
        })) {
            child.WaitForExit();
            if (child.ExitCode != 0 || !File.Exists(Path.Combine(install, "resources", "app.asar")) ||
                !File.Exists(Path.Combine(install, ProgramName)))
                throw new TransitionError("Установка CRM не завершилась. Копия прежней программы и профиль сохранены.");
        }
        PrepareLogin(profile, nativeSession);
    }

    private static void Download(string path) {
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        var request = (HttpWebRequest)WebRequest.Create(ManagerTransitionPayload.Url);
        request.Timeout = 30000;
        request.ReadWriteTimeout = 30000;
        request.MaximumAutomaticRedirections = 5;
        using (var response = (HttpWebResponse)request.GetResponse()) {
            if (response.StatusCode != HttpStatusCode.OK || response.ResponseUri.Scheme != "https" ||
                (response.ContentLength >= 0 && response.ContentLength != ManagerTransitionPayload.Length))
                throw new TransitionError("Сервер вернул неполный пакет обновления.");
            using (var stream = response.GetResponseStream())
            using (var target = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                CopyVerified(stream, target, ManagerTransitionPayload.Length, ManagerTransitionPayload.Sha256);
                target.Flush(true);
            }
        }
    }

    internal static void CopyVerified(Stream source, Stream target, long length, string sha256) {
        using (var hash = SHA256.Create()) {
            var buffer = new byte[128 * 1024];
            long remaining = length;
            while (remaining > 0) {
                int read = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                if (read == 0) break;
                target.Write(buffer, 0, read);
                hash.TransformBlock(buffer, 0, read, buffer, 0);
                remaining -= read;
            }
            hash.TransformFinalBlock(new byte[0], 0, 0);
            if (remaining != 0 || source.ReadByte() != -1 ||
                BitConverter.ToString(hash.Hash).Replace("-", "").ToLowerInvariant() != sha256)
                throw new TransitionError("Контрольная сумма пакета CRM не совпала.");
        }
    }

    internal static Dictionary<string, object> ReadNativeSession(string path) {
        IntPtr db = IntPtr.Zero, statement = IntPtr.Zero;
        try {
            if (!File.Exists(path) || sqlite3_open_v2(Utf8.GetBytes(path + "\0"), out db, 1, IntPtr.Zero) != 0 ||
                sqlite3_prepare_v2(db, Utf8.GetBytes("SELECT data FROM meta WHERE k='session'\0"), -1, out statement, IntPtr.Zero) != 0)
                throw new TransitionError("Не удалось прочитать прежний вход. Профиль сохранён.");
            int state = sqlite3_step(statement);
            if (state == 101) return null;
            int size = state == 100 ? sqlite3_column_bytes(statement, 0) : 0;
            if (size <= 0 || size > 65536) throw new TransitionError("Не удалось прочитать прежний вход. Профиль сохранён.");
            var ciphertext = new byte[size];
            Marshal.Copy(sqlite3_column_blob(statement, 0), ciphertext, 0, size);
            var plaintext = ProtectedData.Unprotect(ciphertext, null, DataProtectionScope.CurrentUser);
            try { return Json.Deserialize<Dictionary<string, object>>(Utf8.GetString(plaintext)); }
            finally { Array.Clear(plaintext, 0, plaintext.Length); }
        } finally {
            if (statement != IntPtr.Zero) sqlite3_finalize(statement);
            if (db != IntPtr.Zero) sqlite3_close(db);
        }
    }

    internal static bool PrepareLogin(string profile, Dictionary<string, object> session) {
        string token = Value(session, "token");
        if (token == "") return false;
        long uid = ValidateSession(session);
        string role = Value(session, "role");
        string settingsPath = Path.Combine(profile, "settings.json");
        string accountPath = Path.Combine(profile, "crm-account.bin");
        CheckPath(settingsPath);
        CheckPath(accountPath);
        var settings = ReadJson(settingsPath);
        if (settings.ContainsKey("crmConnection") || File.Exists(accountPath)) return false;
        var username = Value(session.ContainsKey("device") ? session["device"] as Dictionary<string, object> : null, "username");
        var raw = Utf8.GetBytes(Json.Serialize(new {
            credential = token, uid = uid, role = role, username = username,
            kind = role == "analyst" ? "analyst" : "manager", pending = false, password = false
        }));
        var encoded = new MemoryStream();
        try {
            var header = Utf8.GetBytes("CRM-DPAPI-1\n");
            encoded.Write(header, 0, header.Length);
            var protectedData = ProtectedData.Protect(raw, null, DataProtectionScope.CurrentUser);
            encoded.Write(protectedData, 0, protectedData.Length);
        } finally { Array.Clear(raw, 0, raw.Length); }
        settings["crmConnection"] = new { mode = "remote", source = "crm", server = Primary + "/crm-pilot", auth = "existing-login" };
        settings.Remove("crmLast");
        Directory.CreateDirectory(profile);
        WriteAtomic(accountPath, encoded.ToArray());
        try { WriteAtomic(settingsPath, Utf8.GetBytes(Json.Serialize(settings))); }
        catch { File.Delete(accountPath); throw; }
        return true;
    }

    private static long ValidateSession(Dictionary<string, object> session) {
        string token = Value(session, "token");
        if (token == "") return 0;
        long uid;
        if (Value(session, "product") != "manager" || !TrustedPrimary(Value(session, "server")) ||
            !Regex.IsMatch(token, @"\A[A-Za-z0-9_-]{20,512}\z") ||
            !Int64.TryParse(Value(session, "uid"), out uid) || uid <= 0 || uid > 9007199254740991L ||
            !Regex.IsMatch(Value(session, "role"), @"\A(manager|security|full|analyst)\z"))
            throw new TransitionError("Прежний вход имеет неизвестный формат. Профиль сохранён.");
        return uid;
    }

    private static bool TrustedPrimary(string url) {
        var normalized = url.TrimEnd('/');
        return normalized == Primary || normalized == Primary + ":8080" || normalized == "https://rim-borso.duckdns.org";
    }

    private static Dictionary<string, object> ReadJson(string file) {
        CheckPath(file);
        if (!File.Exists(file)) return new Dictionary<string, object>();
        if (new FileInfo(file).Length > 1024 * 1024) throw new TransitionError("Настройки профиля превышают допустимый размер.");
        return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(file, Utf8));
    }

    private static string Value(Dictionary<string, object> obj, string key) {
        object value;
        return obj != null && obj.TryGetValue(key, out value) ? Convert.ToString(value) : "";
    }

    private static void WriteAtomic(string target, byte[] source) {
        string temporary = target + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try {
            using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                file.Write(source, 0, source.Length);
                file.Flush(true);
            }
            if (File.Exists(target)) File.Replace(temporary, target, null);
            else File.Move(temporary, target);
        } finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    private static void CopyDirectory(string source, string target) {
        CheckPath(source);
        Directory.CreateDirectory(target);
        foreach (string file in Directory.EnumerateFiles(source)) {
            CheckPath(file);
            File.Copy(file, Path.Combine(target, Path.GetFileName(file)));
        }
        foreach (string folder in Directory.EnumerateDirectories(source))
            CopyDirectory(folder, Path.Combine(target, Path.GetFileName(folder)));
    }

    private static void CheckPath(string path) {
        for (var current = Path.GetFullPath(path); current != null; current = Path.GetDirectoryName(current))
            if ((Directory.Exists(current) || File.Exists(current)) &&
                (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new TransitionError("Обнаружена перенаправленная папка. Обновление остановлено.");
    }

    private sealed class TransitionError : Exception {
        internal TransitionError(string message) : base(message) { }
    }
}
