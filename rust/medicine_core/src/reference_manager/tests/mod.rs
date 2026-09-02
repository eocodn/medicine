use super::{
    ReferenceBootstrapPreparation, ReferenceDatabaseValidator, ReferenceManager,
    ReferenceReleaseSource, ReferenceRuntimeError, ReferenceSelection,
};

trait TestReferenceManagerExt {
    fn ensure_installed(&self) -> Result<ReferenceSelection, ReferenceRuntimeError>;
    fn open_installed(&self) -> Result<ReferenceSelection, ReferenceRuntimeError>;
}

impl<S: ReferenceReleaseSource, V: ReferenceDatabaseValidator> TestReferenceManagerExt
    for ReferenceManager<S, V>
{
    fn ensure_installed(&self) -> Result<ReferenceSelection, ReferenceRuntimeError> {
        let preparation = self.prepare_bootstrap()?;
        let mut observer = || Ok(());
        self.install_prepared_with_observer(preparation, &mut observer)
    }

    fn open_installed(&self) -> Result<ReferenceSelection, ReferenceRuntimeError> {
        match self.prepare_bootstrap()? {
            ReferenceBootstrapPreparation::Ready(selection) => Ok(selection),
            _ => Err(ReferenceRuntimeError::from_message(
                "test expected an installed reference",
            )),
        }
    }
}

mod contract;
mod integration;
