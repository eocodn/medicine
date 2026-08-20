const MutationInvariants = Object.freeze({
  MAX_AMBIGUOUS_DOSE_COMPENSATIONS: 1,

  failureKind(error) {
    // Only an explicit client response proves the mutation was rejected.
    // Missing/5xx responses stay ambiguous because the durable write may have committed.
    const status = Number(error?.status);
    return Number.isFinite(status) && status >= 400 && status < 500 ? "definitive" : "ambiguous";
  },

  isActiveOrigin(activePersonId, originPersonId) {
    return Boolean(originPersonId) && activePersonId === originPersonId;
  },

  isDoseDesiredStatus(status) {
    return status === "planned" || status === "taken" || status === "skipped";
  },

  doseConverged(updated, desiredStatus) {
    return updated?.deleted === true || updated?.status === desiredStatus;
  },

  canCompensateAmbiguousDose(completedCompensations) {
    return Number(completedCompensations) < this.MAX_AMBIGUOUS_DOSE_COMPENSATIONS;
  },
});