using System.Windows.Controls;
using YukkuriMovieMaker.Plugin;

namespace BingduYmmBridge;

[PluginDetails(AuthorName = "冰读")]
public sealed class BingduBridgePlugin : IToolPlugin
{
    public BingduBridgePlugin() => BridgeServer.ScheduleStart();
    public string Name => "冰读配音桥";
    public Type ViewModelType => typeof(BridgeViewModel);
    public Type ViewType => typeof(BridgeView);
}

public sealed class BridgeViewModel
{
    public string Status => "冰读配音桥已启动，仅监听本机连接。";
}

public sealed class BridgeView : UserControl
{
    public BridgeView()
    {
        Content = new TextBlock { Text = "冰读配音桥已启动。关闭 YMM4 即可停止。", Margin = new System.Windows.Thickness(16) };
    }
}
