using System.Collections;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows;
using NAudio.Wave;

namespace BingduYmmBridge;

internal static class BridgeServer
{
    private static readonly object StartGate = new();
    private static readonly SemaphoreSlim SynthesisGate = new(1, 1);
    private static HttpListener? listener;
    private static string token = "";
    private static int startScheduled;

    public static void ScheduleStart()
    {
        if (Interlocked.Exchange(ref startScheduled, 1) != 0) return;
        _ = Task.Run(async () =>
        {
            for (var attempt = 0; attempt < 240; attempt++)
            {
                try
                {
                    var hostReady = Application.Current?.Dispatcher.Invoke(() => FindMainViewModel() != null) == true;
                    if (hostReady)
                    {
                        Start();
                        return;
                    }
                }
                catch { }
                await Task.Delay(500);
            }
            Interlocked.Exchange(ref startScheduled, 0);
        });
    }

    private static void Start()
    {
        lock (StartGate)
        {
            if (listener != null) return;
            token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
            for (var port = 18765; port < 18785; port++)
            {
                try
                {
                    var candidate = new HttpListener();
                    candidate.Prefixes.Add($"http://127.0.0.1:{port}/");
                    candidate.Start();
                    listener = candidate;
                    WriteConnectionFile(port);
                    _ = Task.Run(() => Listen(candidate));
                    return;
                }
                catch (HttpListenerException) { }
            }
        }
    }

    private static void WriteConnectionFile(int port)
    {
        var directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "BingduYmmBridge");
        Directory.CreateDirectory(directory);
        File.WriteAllText(Path.Combine(directory, "connection.json"), JsonSerializer.Serialize(new
        {
            api_base = $"http://127.0.0.1:{port}", token, process_id = Environment.ProcessId
        }));
    }

    private static async Task Listen(HttpListener active)
    {
        while (active.IsListening)
        {
            HttpListenerContext context;
            try { context = await active.GetContextAsync(); }
            catch { return; }
            _ = Task.Run(() => Handle(context));
        }
    }

    private static async Task Handle(HttpListenerContext context)
    {
        try
        {
            if (!CryptographicOperations.FixedTimeEquals(
                    Encoding.UTF8.GetBytes(context.Request.Headers["X-Bingdu-Token"] ?? ""),
                    Encoding.UTF8.GetBytes(token)))
            {
                await Reply(context, 401, new { success = false, error = "unauthorized" });
                return;
            }
            if (context.Request.HttpMethod == "GET" && context.Request.Url?.AbsolutePath == "/status")
            {
                await Reply(context, 200, new { success = true, app = "bingdu-ymm-bridge" });
                return;
            }
            if (context.Request.HttpMethod == "POST" && context.Request.Url?.AbsolutePath == "/synthesize")
            {
                using var document = await JsonDocument.ParseAsync(context.Request.InputStream);
                var root = document.RootElement;
                var text = root.GetProperty("text").GetString()?.Trim() ?? "";
                var character = root.GetProperty("character").GetString()?.Trim() ?? "";
                var output = root.GetProperty("output").GetString()?.Trim() ?? "";
                var playbackRate = root.TryGetProperty("playback_rate", out var rateValue) ? rateValue.GetInt32() : 100;
                var volume = root.TryGetProperty("volume", out var volumeValue) ? volumeValue.GetInt32() : 100;
                if (text.Length == 0 || text.Length > 500 || character.Length == 0 || !Path.IsPathFullyQualified(output))
                    throw new ArgumentException("invalid synthesis request");
                if (playbackRate is < 50 or > 200 || volume is < 0 or > 200) throw new ArgumentException("invalid voice settings");
                var result = await Synthesize(text, character, Path.GetFullPath(output), playbackRate, volume);
                await Reply(context, 200, result);
                return;
            }
            await Reply(context, 404, new { success = false, error = "not found" });
        }
        catch (Exception ex)
        {
            await Reply(context, 500, new { success = false, error = ex.InnerException?.Message ?? ex.Message });
        }
    }

    private static async Task<object> Synthesize(string text, string characterName, string output, int playbackRate, int volume)
    {
        await SynthesisGate.WaitAsync();
        var synthesisStarted = DateTime.UtcNow;
        object? model = null;
        object? added = null;
        try
        {
            var add = await OnUi(async () =>
            {
                var main = FindMainViewModel() ?? throw new InvalidOperationException("请先打开 YMM4 项目");
                var timeline = Member(main, "ActiveTimelineViewModel") ?? throw new InvalidOperationException("YMM4 时间线不可用");
                model = FindMainModel(main) ?? throw new InvalidOperationException("YMM4 编辑模型不可用");
                var characters = Enumerable(Member(timeline, "Characters")).ToArray();
                var character = characters.SingleOrDefault(item => Member(item, "Name")?.ToString() == characterName)
                    ?? throw new InvalidOperationException($"YMM4 项目中没有角色“{characterName}”");
                var before = Enumerable(Member(timeline, "Items")).Select(UnwrapItem).ToHashSet(ReferenceEqualityComparer.Instance);
                var frame = before.Select(IntFrameEnd).DefaultIfEmpty(0).Max() + 2;
                const int layer = 20;
                var method = model.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                    .Where(value => value.Name == "AddVoiceItemAsync")
                    .OrderBy(value => Math.Abs(value.GetParameters().Length - 5))
                    .FirstOrDefault() ?? throw new MissingMethodException("当前 YMM4 版本没有 AddVoiceItemAsync");
                var pending = method.Invoke(model, BuildAddArguments(method, frame, layer, character, text));
                if (pending is Task task) await task;
                var returned = pending == null ? null : pending.GetType().GetProperty("Result")?.GetValue(pending);
                await Task.Delay(250);
                var current = Enumerable(Member(timeline, "Items")).Select(UnwrapItem).ToArray();
                var newcomers = current.Where(item => !before.Contains(item)).ToArray();
                added = returned == null ? null : UnwrapItem(returned);
                added = (added != null && Convert.ToInt32(Member(added, "Length") ?? 0) > 0 ? added : null)
                    ?? newcomers.FirstOrDefault(item => Convert.ToInt32(Member(item, "Frame") ?? -1) == frame)
                    ?? newcomers.FirstOrDefault(IsVoice)
                    ?? current.Where(IsMatchingVoice).OrderByDescending(item => Convert.ToInt32(Member(item, "Frame") ?? 0)).FirstOrDefault()
                    ?? current.OrderByDescending(item => Convert.ToInt32(Member(item, "Frame") ?? 0)).FirstOrDefault(item => Convert.ToInt32(Member(item, "Frame") ?? -1) == frame)
                    ?? throw new InvalidOperationException("YMM4 未生成语音项目");
                SetAnimatedValue(added, "PlaybackRate2", playbackRate);
                SetAnimatedValue(added, "Volume", volume);
                var length = Convert.ToInt32(Member(added, "Length") ?? 0);
                if (length <= 1) throw new InvalidOperationException("YMM4 返回的语音长度无效");
                return (main, frame, length);

                bool IsMatchingVoice(object item)
                {
                    if (!IsVoice(item)) return false;
                    var serif = Member(item, "Serif")?.ToString() ?? Member(item, "Text")?.ToString() ?? "";
                    return serif == text;
                }

                static bool IsVoice(object item) => item.GetType().Name.Contains("Voice", StringComparison.OrdinalIgnoreCase);
            });

            var generatedWave = await FindGeneratedWave(synthesisStarted, add.length / 60d);
            if (generatedWave != null)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(output)!);
                WriteAdjustedWave(generatedWave, output, playbackRate / 100d, volume / 100f);
                return new { success = true, output, frames = add.length, bytes = new FileInfo(output).Length };
            }

            var preview = await OnUi(() => Task.FromResult(FindPreview(add.main)
                ?? throw new InvalidOperationException("YMM4 预览播放器不可用")));
            await InvokeAsync(preview, "SeekAsync", add.frame);
            await Task.Delay(250);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            using var capture = new WasapiLoopbackCapture();
            var chunks = new List<byte[]>();
            var chunksGate = new object();
            var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            capture.DataAvailable += (_, args) =>
            {
                var copy = args.Buffer.AsSpan(0, args.BytesRecorded).ToArray();
                lock (chunksGate) chunks.Add(copy);
            };
            capture.RecordingStopped += (_, _) => stopped.TrySetResult();
            capture.StartRecording();
            await InvokeAsync(preview, "TogglePlayAsync");
            var durationMs = Math.Clamp((int)Math.Ceiling(add.length * 1000d / 60d) + 700, 700, 120000);
            await Task.Delay(durationMs);
            try { await InvokeAsync(preview, "StopAsync"); } catch { }
            capture.StopRecording();
            await Task.WhenAny(stopped.Task, Task.Delay(2000));
            byte[] audio;
            lock (chunksGate) audio = chunks.SelectMany(value => value).ToArray();
            using (var writer = new WaveFileWriter(output, capture.WaveFormat)) writer.Write(audio, 0, audio.Length);
            return new { success = true, output, frames = add.length, bytes = audio.Length };
        }
        finally
        {
            if (model != null && added != null)
            {
                try
                {
                    await OnUi(() =>
                    {
                        var remove = model.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                            .FirstOrDefault(value => value.Name == "RemoveItem" && value.GetParameters().Length == 1);
                        remove?.Invoke(model, new[] { added });
                        return Task.CompletedTask;
                    });
                }
                catch { }
            }
            SynthesisGate.Release();
        }
    }

    private static async Task<string?> FindGeneratedWave(DateTime startedAt, double expectedSeconds)
    {
        var tempRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "YukkuriMovieMaker", "v4", "temp");
        for (var attempt = 0; attempt < 20; attempt++)
        {
            var candidates = new List<(string Path, double Difference)>();
            try
            {
                foreach (var path in Directory.EnumerateFiles(tempRoot, "*", SearchOption.AllDirectories))
                {
                    var info = new FileInfo(path);
                    if (info.LastWriteTimeUtc < startedAt.AddSeconds(-1) || info.Length < 44) continue;
                    try
                    {
                        using var reader = new WaveFileReader(path);
                        candidates.Add((path, Math.Abs(reader.TotalTime.TotalSeconds - expectedSeconds)));
                    }
                    catch { }
                }
            }
            catch { }
            var best = candidates.OrderBy(value => value.Difference).FirstOrDefault();
            if (best.Path != null && best.Difference <= 0.35) return best.Path;
            await Task.Delay(100);
        }
        return null;
    }

    private static void WriteAdjustedWave(string sourcePath, string outputPath, double speed, float volume)
    {
        using var source = new WaveFileReader(sourcePath);
        var provider = source.ToSampleProvider();
        var samples = new List<float>();
        var buffer = new float[8192];
        int read;
        while ((read = provider.Read(buffer.AsSpan())) > 0)
            samples.AddRange(buffer.AsSpan(0, read).ToArray());

        var channels = provider.WaveFormat.Channels;
        var sourceFrames = samples.Count / channels;
        if (sourceFrames < 2) throw new InvalidOperationException("YMM4 生成的语音缓存过短");
        var outputFrames = Math.Max(1, (int)Math.Floor((sourceFrames - 1) / speed));
        using var writer = new WaveFileWriter(outputPath, WaveFormat.CreateIeeeFloatWaveFormat(provider.WaveFormat.SampleRate, channels));
        for (var outputFrame = 0; outputFrame < outputFrames; outputFrame++)
        {
            var sourcePosition = outputFrame * speed;
            var leftFrame = Math.Min((int)sourcePosition, sourceFrames - 1);
            var rightFrame = Math.Min(leftFrame + 1, sourceFrames - 1);
            var fraction = (float)(sourcePosition - leftFrame);
            for (var channel = 0; channel < channels; channel++)
            {
                var left = samples[leftFrame * channels + channel];
                var right = samples[rightFrame * channels + channel];
                writer.WriteSample((left + (right - left) * fraction) * volume);
            }
        }
    }

    private static object?[] BuildAddArguments(MethodInfo method, int frame, int layer, object character, string text)
    {
        var parameters = method.GetParameters();
        var values = new object?[parameters.Length];
        for (var i = 0; i < parameters.Length; i++)
        {
            var type = parameters[i].ParameterType;
            if (i == 0 && type == typeof(int)) values[i] = frame;
            else if (i == 1 && type == typeof(int)) values[i] = layer;
            else if (i == 2) values[i] = character;
            else if (type == typeof(string)) values[i] = text;
            else if (type.IsArray) values[i] = Array.CreateInstance(type.GetElementType()!, 0);
            else if (type.IsGenericType && typeof(IEnumerable).IsAssignableFrom(type))
                values[i] = Activator.CreateInstance(typeof(List<>).MakeGenericType(type.GetGenericArguments()[0]));
            else values[i] = parameters[i].HasDefaultValue ? parameters[i].DefaultValue : type.IsValueType ? Activator.CreateInstance(type) : null;
        }
        return values;
    }

    private static async Task InvokeAsync(object target, string name, params object[] arguments)
    {
        var candidates = target.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
            .Where(value => value.Name == name && value.GetParameters().Length == arguments.Length)
            .ToArray();
        var method = candidates.FirstOrDefault(value => value.GetParameters().Select(parameter => parameter.ParameterType)
                .Zip(arguments, (type, argument) => argument == null ? !type.IsValueType : type.IsInstanceOfType(argument))
                .All(matches => matches))
            ?? throw new MissingMethodException(target.GetType().Name, $"{name}({string.Join(",", arguments.Select(value => value?.GetType().Name ?? "null"))})");
        var result = await OnUi(() => Task.FromResult(method.Invoke(target, arguments)));
        if (result is Task task) await task;
    }

    private static object? FindMainViewModel() => Application.Current.Windows.OfType<Window>()
        .Select(window => window.DataContext)
        .FirstOrDefault(value => value != null && Member(value, "ActiveTimelineViewModel") != null);

    private static object? FindMainModel(object main)
    {
        return main.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic)
                   .FirstOrDefault(field => field.FieldType.Name.Contains("MainModel"))?.GetValue(main)
            ?? main.GetType().GetProperties(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                   .FirstOrDefault(property => property.PropertyType.Name.Contains("MainModel"))?.GetValue(main);
    }

    private static object? FindPreview(object main)
    {
        foreach (var area in Enumerable(Member(main, "AnchorableAreaViewModels")))
        {
            var candidate = Member(area, "ViewModel") ?? area;
            if (candidate.GetType().Name == "PreviewViewModel") return candidate;
        }
        return null;
    }

    private static object UnwrapItem(object value) => Member(value, "Item") ?? value;
    private static void SetAnimatedValue(object item, string propertyName, double value)
    {
        var animation = Member(item, propertyName);
        var values = Enumerable(animation == null ? null : Member(animation, "Values")).ToArray();
        if (values.Length == 0) return;
        var property = values[0].GetType().GetProperty("Value", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (property?.CanWrite == true) property.SetValue(values[0], Convert.ChangeType(value, property.PropertyType));
    }
    private static int IntFrameEnd(object item) => Convert.ToInt32(Member(item, "Frame") ?? 0) + Math.Max(0, Convert.ToInt32(Member(item, "Length") ?? 0));
    private static IEnumerable<object> Enumerable(object? value) => value is IEnumerable items ? items.Cast<object>() : Array.Empty<object>();
    private static object? Member(object value, string name)
    {
        var type = value.GetType();
        return type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(value)
            ?? type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(value);
    }

    private static Task<T> OnUi<T>(Func<Task<T>> work)
    {
        var dispatcher = Application.Current.Dispatcher;
        return dispatcher.CheckAccess() ? work() : dispatcher.InvokeAsync(work).Task.Unwrap();
    }

    private static Task OnUi(Func<Task> work)
    {
        var dispatcher = Application.Current.Dispatcher;
        return dispatcher.CheckAccess() ? work() : dispatcher.InvokeAsync(work).Task.Unwrap();
    }

    private static async Task Reply(HttpListenerContext context, int status, object body)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(body);
        context.Response.StatusCode = status;
        context.Response.ContentType = "application/json; charset=utf-8";
        context.Response.ContentLength64 = bytes.Length;
        await context.Response.OutputStream.WriteAsync(bytes);
        context.Response.Close();
    }
}
