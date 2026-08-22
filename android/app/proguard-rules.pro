# JavaScript calls these members by their source-level names through WebView.
# R8 may otherwise rename or remove methods which have no direct JVM caller.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Rust exports JNI symbols using this class and method names verbatim.
-keep class com.medicine.android.MedicineNativeCore {
    *;
}

# Rust calls these observer methods by their source-level names and JNI
# descriptors while rebuilding large reference artifacts.
-keep interface com.medicine.android.NativeReferenceArtifactObserver {
    *;
}
-keep,allowoptimization class * implements com.medicine.android.NativeReferenceArtifactObserver {
    public void progress(java.lang.String, long, long);
    public void checkpoint(java.lang.String);
}
