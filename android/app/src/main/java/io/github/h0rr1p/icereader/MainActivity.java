package io.github.h0rr1p.icereader;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.util.Base64;
import java.io.ByteArrayInputStream;
import java.util.HashMap;
import org.json.JSONObject;

public class MainActivity extends BridgeActivity {
    @Override public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ReaderPlugin.class);
        super.onCreate(savedInstanceState);
        androidx.work.Constraints constraints = new androidx.work.Constraints.Builder()
            .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build();
        androidx.work.PeriodicWorkRequest sync = new androidx.work.PeriodicWorkRequest.Builder(SyncWorker.class, 30, java.util.concurrent.TimeUnit.MINUTES)
            .setConstraints(constraints).setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 30, java.util.concurrent.TimeUnit.SECONDS).build();
        androidx.work.WorkManager.getInstance(this).enqueueUniquePeriodicWork("bingdu-learning-sync", androidx.work.ExistingPeriodicWorkPolicy.KEEP, sync);
        getBridge().setWebViewClient(new BridgeWebViewClient(getBridge()) {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String path = request.getUrl().getEncodedPath();
                if ("localhost".equals(request.getUrl().getHost()) && path.startsWith("/api/")) {
                    try {
                        String query = request.getUrl().getEncodedQuery();
                        JSONObject result = ReaderRuntime.request(MainActivity.this, "GET", path + (query == null ? "" : "?" + query), "{}", "");
                        JSONObject jsonHeaders = result.getJSONObject("headers");
                        HashMap<String, String> headers = new HashMap<>();
                        jsonHeaders.keys().forEachRemaining(k -> headers.put(k, jsonHeaders.optString(k)));
                        String type = headers.getOrDefault("content-type", "application/octet-stream").split(";")[0];
                        return new WebResourceResponse(type, "UTF-8", result.getInt("status"), "Response", headers,
                            new ByteArrayInputStream(Base64.decode(result.getString("body"), Base64.DEFAULT)));
                    } catch (Exception error) {
                        return new WebResourceResponse("text/plain", "UTF-8", 503, "Unavailable", new HashMap<>(),
                            new ByteArrayInputStream("本地资源暂时无法读取".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
                    }
                }
                return super.shouldInterceptRequest(view, request);
            }
        });
    }
}
