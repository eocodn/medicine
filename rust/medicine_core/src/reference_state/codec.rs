use super::{
    validate_state, ReferenceFileSeal, ReferenceStateError, ReferenceStoreState, ReferenceVersion,
    LEGACY_SCHEMA_VERSION, MAGIC_V1, MAGIC_V3,
};

pub struct ReferenceStateCodec;

impl ReferenceStateCodec {
    pub fn encode(state: &ReferenceStoreState) -> Result<Vec<u8>, ReferenceStateError> {
        validate_state(state)?;
        let mut output = Vec::new();
        // DataOutputStream.writeUTF writes a two-byte big-endian length before
        // the UTF-8 magic. Keeping this framing lets Android read Rust state.
        put_utf(&mut output, MAGIC_V3)?;
        output.extend_from_slice(&state.highest_activated_sequence.to_be_bytes());
        output.extend_from_slice(&state.highest_seen_root_sequence.to_be_bytes());
        put_utf(
            &mut output,
            state.highest_seen_root_hash.as_deref().unwrap_or(""),
        )?;
        output.extend_from_slice(&state.highest_retired_contract_major.to_be_bytes());
        put_version(&mut output, state.active.as_ref())?;
        put_seal(&mut output, state.active_seal.as_ref())?;
        put_version(&mut output, state.previous.as_ref())?;
        put_seal(&mut output, state.previous_seal.as_ref())?;
        put_version(&mut output, state.pending.as_ref())?;
        put_seal(&mut output, state.pending_seal.as_ref())?;
        Ok(output)
    }

    pub fn decode(bytes: &[u8]) -> Result<ReferenceStoreState, ReferenceStateError> {
        let mut input = Cursor::new(bytes);
        let magic = input.utf()?;
        let state = match magic.as_str() {
            MAGIC_V1 => {
                let high_water = input.i64()?;
                let active = input.legacy_version()?;
                let previous = input.legacy_version()?;
                let pending = input.legacy_version()?;
                ReferenceStoreState {
                    active,
                    previous,
                    pending,
                    highest_activated_sequence: high_water,
                    ..ReferenceStoreState::default()
                }
            }
            MAGIC_V3 => {
                let high_water = input.i64()?;
                let root_high_water = input.i64()?;
                let root_hash = input.utf()?;
                let retired = input.i32()?;
                let active = input.version()?;
                let active_seal = input.seal()?;
                let previous = input.version()?;
                let previous_seal = input.seal()?;
                let pending = input.version()?;
                let pending_seal = input.seal()?;
                ReferenceStoreState {
                    active,
                    previous,
                    pending,
                    highest_activated_sequence: high_water,
                    highest_seen_root_sequence: root_high_water,
                    highest_seen_root_hash: (!root_hash.is_empty()).then_some(root_hash),
                    highest_retired_contract_major: retired,
                    active_seal,
                    previous_seal,
                    pending_seal,
                }
            }
            _ => {
                return Err(ReferenceStateError::new(
                    "unsupported reference state format",
                ))
            }
        };
        if !input.is_empty() {
            return Err(ReferenceStateError::new("trailing reference state data"));
        }
        validate_state(&state)?;
        Ok(state)
    }

    pub fn is_legacy_v1(bytes: &[u8]) -> bool {
        let mut input = Cursor::new(bytes);
        input.utf().is_ok_and(|magic| magic == MAGIC_V1)
    }
}

fn put_utf(output: &mut Vec<u8>, value: &str) -> Result<(), ReferenceStateError> {
    let bytes = value.as_bytes();
    if bytes.len() > u16::MAX as usize {
        return Err(ReferenceStateError::new(
            "reference state string is too long",
        ));
    }
    output.extend_from_slice(&(bytes.len() as u16).to_be_bytes());
    output.extend_from_slice(bytes);
    Ok(())
}

fn put_version(
    output: &mut Vec<u8>,
    value: Option<&ReferenceVersion>,
) -> Result<(), ReferenceStateError> {
    output.push(u8::from(value.is_some()));
    if let Some(value) = value {
        put_utf(output, &value.dataset_id)?;
        put_utf(output, &value.sha256)?;
        output.extend_from_slice(&value.size_bytes.to_be_bytes());
        output.extend_from_slice(&value.contract_major.to_be_bytes());
        output.extend_from_slice(&value.release_sequence.to_be_bytes());
    }
    Ok(())
}

fn put_seal(
    output: &mut Vec<u8>,
    value: Option<&ReferenceFileSeal>,
) -> Result<(), ReferenceStateError> {
    output.push(u8::from(value.is_some()));
    if let Some(value) = value {
        output.extend_from_slice(&value.size_bytes.to_be_bytes());
        output.extend_from_slice(&value.modified_marker.to_be_bytes());
        output.extend_from_slice(&value.changed_marker.to_be_bytes());
        put_utf(output, &value.identity_key)?;
        output.push(u8::from(value.writable));
    }
    Ok(())
}

struct Cursor<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Cursor<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], ReferenceStateError> {
        let end = self
            .offset
            .checked_add(length)
            .ok_or_else(|| ReferenceStateError::new("invalid reference state length"))?;
        let value = self
            .bytes
            .get(self.offset..end)
            .ok_or_else(|| ReferenceStateError::new("truncated reference state"))?;
        self.offset = end;
        Ok(value)
    }

    fn utf(&mut self) -> Result<String, ReferenceStateError> {
        let length = u16::from_be_bytes(self.take(2)?.try_into().unwrap()) as usize;
        let bytes = self.take(length)?;
        String::from_utf8(bytes.to_vec())
            .map_err(|_| ReferenceStateError::new("invalid reference state UTF-8"))
    }

    fn i64(&mut self) -> Result<i64, ReferenceStateError> {
        Ok(i64::from_be_bytes(self.take(8)?.try_into().unwrap()))
    }

    fn i32(&mut self) -> Result<i32, ReferenceStateError> {
        Ok(i32::from_be_bytes(self.take(4)?.try_into().unwrap()))
    }

    fn flag(&mut self) -> Result<bool, ReferenceStateError> {
        match self.take(1)?[0] {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(ReferenceStateError::new("invalid reference state boolean")),
        }
    }

    fn version(&mut self) -> Result<Option<ReferenceVersion>, ReferenceStateError> {
        if !self.flag()? {
            return Ok(None);
        }
        Ok(Some(ReferenceVersion {
            dataset_id: self.utf()?,
            sha256: self.utf()?,
            size_bytes: self.i64()?,
            contract_major: self.i32()?,
            release_sequence: self.i64()?,
        }))
    }

    fn legacy_version(&mut self) -> Result<Option<ReferenceVersion>, ReferenceStateError> {
        if !self.flag()? {
            return Ok(None);
        }
        let dataset_id = self.utf()?;
        let sha256 = self.utf()?;
        let size_bytes = self.i64()?;
        if self.utf()? != LEGACY_SCHEMA_VERSION {
            return Err(ReferenceStateError::new(
                "unsupported legacy reference schema version",
            ));
        }
        Ok(Some(ReferenceVersion {
            dataset_id,
            sha256,
            size_bytes,
            contract_major: 1,
            release_sequence: self.i64()?,
        }))
    }

    fn seal(&mut self) -> Result<Option<ReferenceFileSeal>, ReferenceStateError> {
        if !self.flag()? {
            return Ok(None);
        }
        Ok(Some(ReferenceFileSeal {
            size_bytes: self.i64()?,
            modified_marker: self.i64()?,
            changed_marker: self.i64()?,
            identity_key: self.utf()?,
            writable: self.flag()?,
        }))
    }

    fn is_empty(&self) -> bool {
        self.offset == self.bytes.len()
    }
}
