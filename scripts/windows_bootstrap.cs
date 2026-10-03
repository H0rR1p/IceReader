using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

internal static class BingduBootstrap
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr handle, string text, string caption, uint type);

    [STAThread]
    private static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string executable = null;
        string buildRoot = Path.GetFullPath(Path.Combine(root, "build")) + Path.DirectorySeparatorChar;
        string pointer = Path.Combine(buildRoot, "desktop-current.txt");
        if (File.Exists(pointer))
        {
            string candidate = Path.GetFullPath(Path.Combine(buildRoot, File.ReadAllText(pointer).Trim(), "win-unpacked", "冰读.exe"));
            if (candidate.StartsWith(buildRoot, StringComparison.OrdinalIgnoreCase) && IsComplete(candidate)) executable = candidate;
        }
        foreach (string directory in new[] { "install-release", "desktop-release" })
        {
            string candidate = Path.Combine(buildRoot, directory, "win-unpacked", "冰读.exe");
            if (executable == null && IsComplete(candidate)) executable = candidate;
        }
        if (executable == null)
        {
            MessageBox(IntPtr.Zero, "没有找到目录版冰读，请先运行 scripts\\build_windows.ps1。", "冰读启动失败", 0x10);
            return;
        }

        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = executable,
                WorkingDirectory = Path.GetDirectoryName(executable),
                UseShellExecute = true,
            });
        }
        catch (Exception error)
        {
            MessageBox(IntPtr.Zero, error.Message, "冰读启动失败", 0x10);
        }
    }

    private static bool IsComplete(string executable)
    {
        string directory = Path.GetDirectoryName(executable);
        return File.Exists(executable)
            && File.Exists(Path.Combine(directory, "icudtl.dat"))
            && File.Exists(Path.Combine(directory, "resources", "app.asar"))
            && File.Exists(Path.Combine(directory, "resources", "backend", "bingdu-service.exe"));
    }
}
