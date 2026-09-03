package com.medicine.android

internal object WebViewCompatibility {
    const val MINIMUM_SUPPORTED_MAJOR = 93

    // Keep this probe parseable by runtimes older than the supported floor. The
    // version gate runs first, while these checks verify the concrete APIs used
    // by the shared UI and the packaged ONNX Runtime WebAssembly build.
    const val CAPABILITY_PROBE = """
        (function () {
            try {
                if (!Function("var value = { nested: 1 }; return value?.nested === 1;")()) return false;
                if (!Function("var value = null; value ??= 2; return value === 2;")()) return false;
                if (typeof Object.hasOwn !== "function") return false;
                if (typeof Array.prototype.at !== "function") return false;
                if (typeof String.prototype.replaceAll !== "function") return false;
                if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") return false;
                if (typeof WebAssembly === "undefined" || typeof WebAssembly.validate !== "function") return false;
                return WebAssembly.validate(new Uint8Array([
                    0, 97, 115, 109, 1, 0, 0, 0, 1, 4, 1, 96, 0, 0, 3, 2, 1, 0,
                    10, 30, 1, 28, 0, 65, 0, 253, 15, 253, 12, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 253, 186, 1, 26, 11
                ]));
            } catch (error) {
                return false;
            }
        })()
    """

    fun isSupportedVersion(versionName: String?): Boolean {
        val major = versionName
            ?.trim()
            ?.substringBefore('.')
            ?.toIntOrNull()
            ?: return false
        return major >= MINIMUM_SUPPORTED_MAJOR
    }

    fun capabilitiesSatisfied(result: String?): Boolean = result == "true"
}