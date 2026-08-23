package com.medicine.android

sealed interface ReferenceBootstrapPlan {
    data class Download(
        val target: ReferenceVersion,
        val full: ReferenceReleaseArtifact,
    ) : ReferenceBootstrapPlan
}

sealed interface ReferenceUpdatePlan {
    data object UpToDate : ReferenceUpdatePlan
    data object RollbackRejected : ReferenceUpdatePlan
    data object IdentityConflict : ReferenceUpdatePlan
    data class Stage(
        val target: ReferenceVersion,
        val primary: ReferenceReleaseArtifact,
        val fallbackFull: ReferenceReleaseArtifact?,
    ) : ReferenceUpdatePlan
}

interface ReferenceLifecyclePlanner {
    fun planBootstrap(
        expectedContractMajor: Int,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceBootstrapPlan

    fun planUpdate(
        current: ReferenceVersion,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceUpdatePlan
}

object RustReferenceLifecyclePlanner : ReferenceLifecyclePlanner {
    override fun planBootstrap(
        expectedContractMajor: Int,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceBootstrapPlan = ReferenceNativeCore.planReferenceBootstrap(
        expectedContractMajor,
        highestActivatedSequence,
        release,
    )

    override fun planUpdate(
        current: ReferenceVersion,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceUpdatePlan = ReferenceNativeCore.planReferenceUpdate(
        current,
        highestActivatedSequence,
        release,
    )
}