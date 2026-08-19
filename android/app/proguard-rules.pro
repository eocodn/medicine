# JavaScript calls these members by their source-level names through WebView.
# R8 may otherwise rename or remove methods which have no direct JVM caller.
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}