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
        string executable = Path.Combine(root, "build", "release", "冰读", "冰读.exe");
        if (!File.Exists(executable))
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
}
