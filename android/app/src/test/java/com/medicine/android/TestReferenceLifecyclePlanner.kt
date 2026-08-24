package com.medicine.android

internal object TestReferenceLifecyclePlanner : ReferenceLifecyclePlanner {
    override fun planBootstrap(
        expectedContractMajor: Int,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceBootstrapPlan {
        require(release.contractMajor == expectedContractMajor) {
            "reference release contract is incompatible with this runtime"
        }
        require(release.releaseSequence >= highestActivatedSequence) {
            "reference rollback is not allowed"
        }
        return ReferenceBootstrapPlan.Download(release.targetVersion(), release.full)
    }

    override fun planUpdate(
        current: ReferenceVersion,
        highestActivatedSequence: Long,
        release: VerifiedReferenceRelease,
    ): ReferenceUpdatePlan {
        require(release.contractMajor == current.contractMajor) {
            "reference release contract does not match installed runtime"
        }
        if (release.releaseSequence < highestActivatedSequence) {
            return ReferenceUpdatePlan.RollbackRejected
        }
        if (
            current.sha256 == release.targetSha256 &&
            current.sizeBytes == release.targetSizeBytes &&
            current.datasetId == release.datasetId
        ) {
            return ReferenceUpdatePlan.UpToDate
        }
        if (
            release.releaseSequence == highestActivatedSequence &&
            current.releaseSequence == highestActivatedSequence
        ) {
            return ReferenceUpdatePlan.IdentityConflict
        }
        val patch = release.patches.singleOrNull {
            it.fromSha256 == current.sha256 && it.fromSizeBytes == current.sizeBytes
        }
        return ReferenceUpdatePlan.Stage(
            target = release.targetVersion(),
            primary = patch ?: release.full,
            fallbackFull = release.full.takeIf { patch != null },
        )
    }

    private fun VerifiedReferenceRelease.targetVersion() = ReferenceVersion(
        datasetId = datasetId,
        sha256 = targetSha256,
        sizeBytes = targetSizeBytes,
        contractMajor = contractMajor,
        releaseSequence = releaseSequence,
    )
}