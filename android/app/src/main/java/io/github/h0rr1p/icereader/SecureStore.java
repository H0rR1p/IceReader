package io.github.h0rr1p.icereader;
import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.security.KeyStore;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class SecureStore {
    private final Context context;
    private final SecretKey key;
    public SecureStore(Context context) throws Exception {
        this.context = context;
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        String alias = "bingdu-private-storage";
        if (!store.containsAlias(alias)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        key = (SecretKey) store.getKey(alias, null);
    }
    public synchronized String read(String name) throws Exception {
        String encoded = context.getSharedPreferences("secure", Context.MODE_PRIVATE).getString(name, "");
        if (encoded.isEmpty()) return "";
        byte[] bytes = Base64.decode(encoded, Base64.NO_WRAP);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, bytes, 0, 12));
        return new String(cipher.doFinal(bytes, 12, bytes.length - 12), java.nio.charset.StandardCharsets.UTF_8);
    }
    public synchronized void write(String name, String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key);
        byte[] encrypted = cipher.doFinal(value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        byte[] combined = new byte[12 + encrypted.length];
        System.arraycopy(cipher.getIV(), 0, combined, 0, 12); System.arraycopy(encrypted, 0, combined, 12, encrypted.length);
        context.getSharedPreferences("secure", Context.MODE_PRIVATE).edit().putString(name, Base64.encodeToString(combined, Base64.NO_WRAP)).commit();
    }
    public synchronized String getSecret(String name) throws Exception {
        String value = read(name);
        if (value.isEmpty()) {
            byte[] bytes = new byte[32]; new SecureRandom().nextBytes(bytes);
            value = Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_WRAP); write(name, value);
        }
        return value;
    }
}
