package io.github.h0rr1p.icereader;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import org.json.JSONObject;
import android.util.Base64;

/** Durable learning-data sync. Android controls timing; AI book jobs remain foreground. */
public class SyncWorker extends Worker {
    public SyncWorker(Context context, WorkerParameters parameters) { super(context, parameters); }
    @NonNull @Override public Result doWork() {
        try {
            JSONObject identity = ReaderRuntime.request(getApplicationContext(), "GET", "/api/me", "{}", "");
            JSONObject me = new JSONObject(new String(Base64.decode(identity.getString("body"), Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
            if (!getApplicationContext().getSharedPreferences("android-settings", Context.MODE_PRIVATE).getBoolean("auto-sync:" + me.getString("user_id"), true)) return Result.success();
            JSONObject response = ReaderRuntime.request(getApplicationContext(), "GET", "/api/cloud/status", "{}", "");
            if (response.getInt("status") == 401) return Result.success();
            JSONObject state = new JSONObject(new String(Base64.decode(response.getString("body"), Base64.DEFAULT), java.nio.charset.StandardCharsets.UTF_8));
            if (!state.optBoolean("connected") || !state.optBoolean("auto_sync", true)) return Result.success();
            JSONObject sync = ReaderRuntime.request(getApplicationContext(), "POST", "/api/cloud/sync", "{}", "");
            int status = sync.getInt("status");
            if (status == 429 || status >= 500) return Result.retry();
            return status >= 400 ? Result.failure() : Result.success();
        } catch (Exception e) { return Result.retry(); }
    }
}
