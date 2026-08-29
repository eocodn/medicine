package com.medicine.android;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.provider.MediaStore;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import androidx.activity.ComponentActivity;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.content.FileProvider;
import androidx.webkit.WebViewAssetLoader;
import java.io.File;

final class OcrIntegration {
    private final ComponentActivity activity;
    private final ActivityResultLauncher<Intent> fileChooserLauncher;
    private ValueCallback<Uri[]> fileChooserCallback;
    private Uri pendingCaptureUri;
    private File pendingCaptureFile;

    OcrIntegration(ComponentActivity activity) {
        this.activity = activity;
        this.fileChooserLauncher = activity.registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(),
            result -> {
                ValueCallback<Uri[]> callback = fileChooserCallback;
                fileChooserCallback = null;
                Uri captureUri = pendingCaptureUri;
                File captureFile = pendingCaptureFile;
                pendingCaptureUri = null;
                pendingCaptureFile = null;
                Uri[] picked = result.getResultCode() == Activity.RESULT_OK
                    ? WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData())
                    : null;
                boolean usedCamera = result.getResultCode() == Activity.RESULT_OK
                    && (picked == null || picked.length == 0)
                    && captureUri != null;
                if (!usedCamera && captureFile != null) captureFile.delete();
                if (callback != null) callback.onReceiveValue(usedCamera ? new Uri[] {captureUri} : picked);
            }
        );
    }

    void configureAssetLoader(WebViewAssetLoader.Builder builder) {
        builder.addPathHandler("/ocr-assets/", new WebViewAssetLoader.AssetsPathHandler(activity));
    }

    void configureWebView(WebView webView) {
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(
                WebView ignored,
                ValueCallback<Uri[]> filePathCallback,
                FileChooserParams fileChooserParams
            ) {
                if (fileChooserCallback != null) fileChooserCallback.onReceiveValue(null);
                if (pendingCaptureFile != null) pendingCaptureFile.delete();
                pendingCaptureFile = null;
                pendingCaptureUri = null;
                fileChooserCallback = filePathCallback;
                try {
                    Intent cameraIntent = createCameraCaptureIntent();
                    Intent chooser = Intent.createChooser(fileChooserParams.createIntent(), "처방전 사진 선택");
                    chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[] {cameraIntent});
                    fileChooserLauncher.launch(chooser);
                    return true;
                } catch (Exception error) {
                    if (pendingCaptureFile != null) pendingCaptureFile.delete();
                    pendingCaptureFile = null;
                    pendingCaptureUri = null;
                    fileChooserCallback = null;
                    filePathCallback.onReceiveValue(null);
                    return false;
                }
            }
        });
    }

    void close() {
        if (fileChooserCallback != null) fileChooserCallback.onReceiveValue(null);
        fileChooserCallback = null;
        if (pendingCaptureFile != null) pendingCaptureFile.delete();
        pendingCaptureFile = null;
        pendingCaptureUri = null;
    }

    private Intent createCameraCaptureIntent() throws Exception {
        File directory = new File(activity.getCacheDir(), "ocr-capture");
        directory.mkdirs();
        File[] oldFiles = directory.listFiles();
        if (oldFiles != null) for (File file : oldFiles) file.delete();
        File captureFile = File.createTempFile("prescription-", ".jpg", directory);
        Uri captureUri = FileProvider.getUriForFile(
            activity,
            activity.getPackageName() + ".fileprovider",
            captureFile
        );
        pendingCaptureFile = captureFile;
        pendingCaptureUri = captureUri;
        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        intent.putExtra(MediaStore.EXTRA_OUTPUT, captureUri);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        return intent;
    }
}
