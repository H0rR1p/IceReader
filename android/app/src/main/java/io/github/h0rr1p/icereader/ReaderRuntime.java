package io.github.h0rr1p.icereader;
import android.content.Context;
import com.chaquo.python.Python;
import com.chaquo.python.PyObject;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

public final class ReaderRuntime {
    private static PyObject runtime;
    public static synchronized void initialize(Context context) throws Exception {
        if (runtime != null) return;
        File data = new File(context.getFilesDir(), "data");
        installLegal(context);
        File dictionary = new File(data, "nlp/system_core.dic");
        dictionary.getParentFile().mkdirs();
        if (!dictionary.isFile()) {
            File temporary = new File(dictionary.getParentFile(), "dictionary.tmp");
            try (InputStream input = context.getAssets().open("nlp/system_core.dic"); FileOutputStream output = new FileOutputStream(temporary)) {
                byte[] buffer = new byte[65536]; int size;
                while ((size = input.read(buffer)) != -1) output.write(buffer, 0, size);
                output.getFD().sync();
            }
            if (!temporary.renameTo(dictionary)) throw new java.io.IOException("Cannot install tokenizer dictionary");
        }
        if (!Python.isStarted()) Python.start(new AndroidPlatform(context));
        PyObject module = Python.getInstance().getModule("mobile_runtime");
        module.callAttr("start", data.getAbsolutePath(), new SecureStore(context), context.getApplicationInfo().nativeLibraryDir);
        runtime = module;
    }
    public static JSONObject request(Context context, String method, String path, String headers, String body) throws Exception {
        initialize(context);
        return new JSONObject(runtime.callAttr("request", method, path, headers, body).toString());
    }
    private static void installLegal(Context context) throws Exception {
        File destination = new File(context.getFilesDir(), "legal"); destination.mkdirs();
        byte[] manifest;
        try (InputStream input = context.getAssets().open("legal/release.json"); java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096]; int count; while ((count = input.read(buffer)) != -1) out.write(buffer, 0, count); manifest = out.toByteArray();
        }
        File current = new File(destination, "release.json");
        if (current.isFile() && java.util.Arrays.equals(manifest, java.nio.file.Files.readAllBytes(current.toPath()))) return;
        for (String name : new String[]{"LICENSE", "NOTICE.md", "THIRD-PARTY-NOTICES.txt", "corresponding-source.zip"}) {
            File temporary = new File(destination, name + ".tmp");
            try (InputStream input = context.getAssets().open("legal/" + name); FileOutputStream out = new FileOutputStream(temporary)) {
                byte[] buffer = new byte[65536]; int length;
                while ((length = input.read(buffer)) != -1) out.write(buffer, 0, length);
                out.getFD().sync();
            }
            java.nio.file.Files.move(temporary.toPath(), new File(destination, name).toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        }
        java.nio.file.Files.write(current.toPath(), manifest);
    }
    public static JSONObject request(Context context, String method, String path, String headers, String body, String id) throws Exception {
        initialize(context);
        return new JSONObject(runtime.callAttr("request", method, path, headers, body, id).toString());
    }
    public static void cancel(String id) { if (runtime != null) runtime.callAttr("cancel", id); }
    public static JSONObject upload(Context context, String path, File file, String name, String mime, String fields) throws Exception {
        initialize(context);
        return new JSONObject(runtime.callAttr("upload", path, file.getAbsolutePath(), name, mime, fields).toString());
    }
    public static String export(Context context, String path, String method, String body, File file) throws Exception {
        initialize(context);
        return runtime.callAttr("export_file", path, method, body, file.getAbsolutePath()).toString();
    }
}
