use super::{validate_state, ReferenceStateError, ReferenceStoreState};

pub struct ReferenceStateCodec;

impl ReferenceStateCodec {
    pub fn encode(state: &ReferenceStoreState) -> Result<Vec<u8>, ReferenceStateError> {
        validate_state(state)?;
        serde_json::to_vec(state).map_err(|error| {
            ReferenceStateError::new(format!("cannot encode reference state: {error}"))
        })
    }

    pub fn decode(bytes: &[u8]) -> Result<ReferenceStoreState, ReferenceStateError> {
        let state: ReferenceStoreState = serde_json::from_slice(bytes).map_err(|error| {
            ReferenceStateError::new(format!("cannot decode reference state: {error}"))
        })?;
        validate_state(&state)?;
        Ok(state)
    }
}
