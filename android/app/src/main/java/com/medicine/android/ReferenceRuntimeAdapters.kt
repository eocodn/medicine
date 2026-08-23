package com.medicine.android

import java.io.File

object ReferenceRuntimePolicy {
    // Public app↔DB logical contract. Canonical schema/build policy are server-internal.
    const val CONTRACT_MAJOR = 1
}

class RustReferenceDatabaseVerifier : ReferenceDatabaseVerifier {
    override fun verify(file: File, version: ReferenceVersion) {
        ReferenceNativeCore.verifyDatabase(file, version)
        ReferenceNativeCore.verifyRuntimeMaterialization(file)
    }

    override fun verifyRuntimeCapabilities(file: File, version: ReferenceVersion) {
        ReferenceNativeCore.verifyRuntimeCapabilities(file)
    }
}

class RustReferenceArtifactRebuilder : ReferenceArtifactRebuilder {
    override fun rebuild(
        current: InstalledReferenceVersion?,
        target: ReferenceVersion,
        artifact: ReferenceReleaseArtifact,
        downloaded: File,
        output: File,
        observer: ReferenceUpdateObserver,
    ) {
        ReferenceNativeCore.rebuildArtifact(
            current,
            target,
            artifact,
            downloaded,
            output,
            object : NativeReferenceArtifactObserver {
                override fun progress(phase: String, completedBytes: Long, totalBytes: Long) {
                    observer.progress("rebuild-$phase", completedBytes, totalBytes)
                }

                override fun checkpoint(path: String) {
                    observer.phase("rebuild-checkpoint")
                }
            },
        )
    }
}
