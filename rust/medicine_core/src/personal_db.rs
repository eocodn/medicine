use rusqlite::{Connection, OpenFlags};
use std::path::Path;
use std::time::Duration;

#[derive(Clone, Copy)]
pub(crate) enum Access {
    ReadOnly,
    ReadWrite,
}

pub(crate) enum OpenError {
    Unavailable,
    Sql,
}

pub(crate) fn open(personal_db: Option<&Path>, access: Access) -> Result<Connection, OpenError> {
    let path = personal_db.ok_or(OpenError::Unavailable)?;
    let flags = match access {
        Access::ReadOnly => OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        Access::ReadWrite => OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    };
    let con = Connection::open_with_flags(path, flags).map_err(|_| OpenError::Sql)?;
    con.busy_timeout(Duration::from_secs(5))
        .map_err(|_| OpenError::Sql)?;
    con.pragma_update(None, "foreign_keys", "ON")
        .map_err(|_| OpenError::Sql)?;
    if matches!(access, Access::ReadOnly) {
        con.pragma_update(None, "query_only", "ON")
            .map_err(|_| OpenError::Sql)?;
    }
    Ok(con)
}
