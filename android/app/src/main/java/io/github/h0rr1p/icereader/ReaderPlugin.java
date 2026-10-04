package io.github.h0rr1p.icereader;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.io.File;
import java.io.FileOutputStream;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import android.util.Base64;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import androidx.activity.result.ActivityResult;
import com.getcapacitor.annotation.ActivityCallback;

@CapacitorPlugin(name = "Reader")
public class ReaderPlugin extends Plugin {
    private final ExecutorService workers = Executors.newFixedThreadPool(4);
    private final ConcurrentHashMap<String, File> uploads = new ConcurrentHashMap<>();
    @PluginMethod public void request(PluginCall call) {
        String method = call.getString("method", "GET"), path = call.getString("path", "");
        if (!path.startsWith("/api/") || path.startsWith("/api/voice")) { call.reject("Unsupported request"); return; }
        workers.execute(() -> {
            try { call.resolve(JSObject.fromJSONObject(ReaderRuntime.request(getContext(), method, path,
                call.getString("headers", "{}"), call.getString("body", ""), call.getString("id", "")))); }
            catch (Exception error) { call.reject("本地服务无法完成请求：" + error.getMessage()); }
        });
    }
    @PluginMethod public void cancel(PluginCall call) { ReaderRuntime.cancel(call.getString("id", "")); call.resolve(); }
    @PluginMethod public void setKeepAwake(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            if (Boolean.TRUE.equals(call.getBoolean("enabled", false))) getActivity().getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else getActivity().getWindow().clearFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            call.resolve();
        });
    }
    @PluginMethod public void syncPreferences(PluginCall call) {
        workers.execute(() -> {
            try {
                JSONObject response = ReaderRuntime.request(getContext(), "GET", "/api/me", "{}", "");
                JSONObject me = new JSONObject(new String(Base64.decode(response.getString("body"), Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
                String key = "auto-sync:" + me.getString("user_id");
                android.content.SharedPreferences preferences = getContext().getSharedPreferences("android-settings", android.content.Context.MODE_PRIVATE);
                Boolean value = call.getBoolean("enabled");
                if (value != null) preferences.edit().putBoolean(key, value).apply();
                JSObject result = new JSObject(); result.put("enabled", preferences.getBoolean(key, true)); call.resolve(result);
            } catch (Exception e) { call.reject(e.getMessage()); }
        });
    }
    @PluginMethod public void startOidc(PluginCall call) {
        workers.execute(() -> {
            try {
                java.net.ServerSocket server = new java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"));
                server.setSoTimeout(180000);
                String callbackPath = "/oauth/" + UUID.randomUUID();
                String callback = "http://127.0.0.1:" + server.getLocalPort() + callbackPath;
                String provider = call.getString("provider", "");
                if (!provider.matches("[A-Za-z0-9_-]{1,80}")) { server.close(); throw new Exception("Invalid provider"); }
                JSONObject start = ReaderRuntime.request(getContext(), "POST", "/api/cloud/oidc/start/" + provider,
                    "{\"content-type\":\"application/json\"}", Base64.encodeToString(new org.json.JSONObject().put("callback_url", callback).toString().getBytes(java.nio.charset.StandardCharsets.UTF_8), Base64.NO_WRAP));
                JSONObject body = new JSONObject(new String(Base64.decode(start.getString("body"), Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
                if (start.getInt("status") != 200) { server.close(); throw new Exception(body.optString("detail", "Cannot start login")); }
                android.net.Uri url = android.net.Uri.parse(body.getString("url"));
                if (!"https".equals(url.getScheme())) { server.close(); throw new Exception("云端登录必须使用 HTTPS"); }
                getActivity().runOnUiThread(() -> getActivity().startActivity(new android.content.Intent(android.content.Intent.ACTION_VIEW, url)));
                call.resolve();
                try (server) {
                    long deadline = System.currentTimeMillis() + 180000;
                    while (System.currentTimeMillis() < deadline) {
                        try (java.net.Socket socket = server.accept()) {
                            socket.setSoTimeout(3000);
                            String line = new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream())).readLine();
                            String target = line == null ? "" : line.split(" ")[1];
                            android.net.Uri redirect = android.net.Uri.parse("http://127.0.0.1" + target);
                            if (!callbackPath.equals(redirect.getPath())) { socket.getOutputStream().write("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n".getBytes()); continue; }
                            String code = redirect.getQueryParameter("code");
                            if (code == null || code.length() > 1000) throw new Exception("云端登录已取消");
                            JSONObject complete = ReaderRuntime.request(getContext(), "GET", "/api/cloud/oidc/complete?code=" + java.net.URLEncoder.encode(code, "UTF-8"), "{}", "");
                            if (complete.getJSONObject("headers").optString("location").contains("cloud_error")) throw new Exception("云端登录验证失败");
                            byte[] message = "登录完成，请返回冰读。".getBytes(java.nio.charset.StandardCharsets.UTF_8);
                            socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: " + message.length + "\r\nConnection: close\r\n\r\n").getBytes());
                            socket.getOutputStream().write(message);
                            notifyListeners("accountChanged", new JSObject()); break;
                        }
                    }
                }
            } catch (Exception e) {
                if (!call.isKeptAlive()) call.reject(e.getMessage());
                JSObject error = new JSObject(); error.put("message", e.getMessage()); notifyListeners("oauthError", error);
            }
        });
    }
    @PluginMethod public void uploadStart(PluginCall call) {
        try {
            String id = UUID.randomUUID().toString();
            File folder = new File(getContext().getCacheDir(), "uploads"); folder.mkdirs();
            File file = new File(folder, id); file.createNewFile(); uploads.put(id, file);
            JSObject result = new JSObject(); result.put("id", id); call.resolve(result);
        } catch (Exception e) { call.reject(e.getMessage()); }
    }
    @PluginMethod public void uploadChunk(PluginCall call) {
        workers.execute(() -> {
            File file = uploads.get(call.getString("id", ""));
            if (file == null) { call.reject("Upload expired"); return; }
            try (FileOutputStream out = new FileOutputStream(file, true)) {
                byte[] bytes = Base64.decode(call.getString("body", ""), Base64.DEFAULT);
                if (bytes.length > 524288 || file.length() + bytes.length > 2147483648L) throw new Exception("File too large");
                out.write(bytes); call.resolve();
            } catch (Exception e) { call.reject(e.getMessage()); }
        });
    }
    @PluginMethod public void uploadComplete(PluginCall call) {
        File file = uploads.remove(call.getString("id", ""));
        if (file == null) { call.reject("Upload expired"); return; }
        workers.execute(() -> {
            try { call.resolve(JSObject.fromJSONObject(ReaderRuntime.upload(getContext(), call.getString("path", ""),
                file, call.getString("name", "upload"), call.getString("mime", "application/octet-stream"), call.getString("fields", "{}")))); }
            catch (Exception e) { call.reject(e.getMessage()); }
            finally { file.delete(); }
        });
    }
    @PluginMethod public void uploadCancel(PluginCall call) {
        File file = uploads.remove(call.getString("id", "")); if (file != null) file.delete(); call.resolve();
    }
    @PluginMethod public void exportFile(PluginCall call) {
        workers.execute(() -> {
            try {
                File directory = new File(getContext().getCacheDir(), "exports"); directory.mkdirs();
                File[] old = directory.listFiles();
                if (old != null) for (File file : old) if (file.lastModified() < System.currentTimeMillis() - 86400000) file.delete();
                File temporary = new File(directory, UUID.randomUUID().toString() + ".tmp");
                String filename = ReaderRuntime.export(getContext(), call.getString("path", ""), call.getString("method", "GET"), call.getString("body", ""), temporary);
                filename = filename.replaceAll("[\\\\/:*?\"<>|]", "_");
                File target = new File(directory, UUID.randomUUID().toString().substring(0, 8) + "-" + filename);
                if (!temporary.renameTo(target)) throw new Exception("Cannot save export");
                JSObject result = new JSObject(); result.put("filename", filename);
                result.put("uri", android.net.Uri.fromFile(target).toString()); call.resolve(result);
            } catch (Exception e) { call.reject("无法导出：" + e.getMessage()); }
        });
    }
    @PluginMethod public void saveFile(PluginCall call) {
        android.content.Intent intent = new android.content.Intent(android.content.Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(android.content.Intent.CATEGORY_OPENABLE); intent.setType(call.getString("mime", "application/zip"));
        intent.putExtra(android.content.Intent.EXTRA_TITLE, call.getString("filename", "IceReader.zip"));
        startActivityForResult(call, intent, "saveFileResult");
    }
    @ActivityCallback private void saveFileResult(PluginCall call, ActivityResult result) {
        if (call == null) return;
        if (result.getResultCode() != android.app.Activity.RESULT_OK || result.getData() == null) {
            JSObject cancelled = new JSObject(); cancelled.put("cancelled", true); call.resolve(cancelled); return;
        }
        android.net.Uri destination = result.getData().getData();
        workers.execute(() -> {
            try {
                File source = new File(android.net.Uri.parse(call.getString("uri", "")).getPath()).getCanonicalFile();
                File root = new File(getContext().getCacheDir(), "exports").getCanonicalFile();
                if (!source.getPath().startsWith(root.getPath() + File.separator) || !source.isFile()) throw new Exception("Invalid export path");
                try (java.io.InputStream input = new java.io.FileInputStream(source);
                     java.io.OutputStream output = getContext().getContentResolver().openOutputStream(destination, "wt")) {
                    if (output == null) throw new Exception("Cannot open destination");
                    byte[] buffer = new byte[65536]; int length;
                    while ((length = input.read(buffer)) != -1) output.write(buffer, 0, length);
                }
                call.resolve();
            } catch (Exception e) { call.reject("无法保存：" + e.getMessage()); }
        });
    }
}
