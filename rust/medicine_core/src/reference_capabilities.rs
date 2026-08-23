use rusqlite::{Connection, OpenFlags};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::path::Path;

const SEARCH_DOCUMENT_COLUMNS: &[&str] = &[
    "item_seq",
    "normalized_product_name",
    "normalized_manufacturer",
    "normalized_ingredient_names",
];

#[derive(Debug)]
pub enum ReferenceRuntimeCapabilityError {
    Io(std::io::Error),
    Sqlite(rusqlite::Error),
    ProductSearch(String),
}

impl Display for ReferenceRuntimeCapabilityError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "reference database I/O failed: {error}"),
            Self::Sqlite(error) => write!(
                formatter,
                "reference runtime capability SQLite check failed: {error}"
            ),
            Self::ProductSearch(message) => write!(
                formatter,
                "reference product-search capability verification failed: {message}"
            ),
        }
    }
}

impl Error for ReferenceRuntimeCapabilityError {}

impl From<std::io::Error> for ReferenceRuntimeCapabilityError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<rusqlite::Error> for ReferenceRuntimeCapabilityError {
    fn from(error: rusqlite::Error) -> Self {
        Self::Sqlite(error)
    }
}

pub fn verify_reference_runtime_capabilities(
    path: &Path,
) -> Result<(), ReferenceRuntimeCapabilityError> {
    let connection = open_read_only(path)?;
    verify_product_search_schema(&connection)
}

pub(crate) fn verify_reference_runtime_materialization(
    path: &Path,
) -> Result<(), ReferenceRuntimeCapabilityError> {
    let connection = open_read_only(path)?;
    verify_product_search_materialization(&connection)
}

fn open_read_only(path: &Path) -> Result<Connection, ReferenceRuntimeCapabilityError> {
    std::fs::metadata(path)?;
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    connection.pragma_update(None, "query_only", true)?;
    Ok(connection)
}

pub(crate) fn verify_product_search_schema(
    connection: &Connection,
) -> Result<(), ReferenceRuntimeCapabilityError> {
    let document_kind = object_kind(connection, "product_search_documents")?;
    if document_kind.as_deref() != Some("table") {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(
            "missing table product_search_documents".to_owned(),
        ));
    }
    let document_columns = table_columns(connection, "product_search_documents")?;
    for required in SEARCH_DOCUMENT_COLUMNS {
        if !document_columns.iter().any(|column| column == required) {
            return Err(ReferenceRuntimeCapabilityError::ProductSearch(format!(
                "product_search_documents is missing column {required}"
            )));
        }
    }

    let Some(fts_sql) = object_sql(connection, "product_search_fts")? else {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(
            "missing table product_search_fts".to_owned(),
        ));
    };
    let compact_fts_sql = fts_sql
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>()
        .to_ascii_lowercase();
    if !compact_fts_sql.starts_with("createvirtualtableproduct_search_ftsusingfts5(")
        || !compact_fts_sql.contains("tokenize='trigram'")
        || !compact_fts_sql.contains("content=''")
    {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(
            "product_search_fts is not the required contentless trigram FTS5 accelerator"
                .to_owned(),
        ));
    }
    let fts_columns = table_columns(connection, "product_search_fts")?;
    if !fts_columns.iter().any(|column| column == "searchable_text") {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(
            "product_search_fts is missing column searchable_text".to_owned(),
        ));
    }

    // Prepare the exact MATCH form used by the search path so an artifact that
    // merely has similarly named objects cannot pass the runtime boundary.
    connection.prepare(
        "SELECT rowid FROM product_search_fts WHERE product_search_fts MATCH ?1 LIMIT 0",
    )?;
    Ok(())
}

fn verify_product_search_materialization(
    connection: &Connection,
) -> Result<(), ReferenceRuntimeCapabilityError> {
    verify_product_search_schema(connection)?;

    let product_rows = row_count(connection, "products")?;
    let document_rows = row_count(connection, "product_search_documents")?;
    if document_rows != product_rows {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(format!(
            "product_search_documents row count {document_rows} does not match products {product_rows}"
        )));
    }
    let fts_rows = row_count(connection, "product_search_fts")?;
    if fts_rows != document_rows {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(format!(
            "product_search_fts row count {fts_rows} does not match documents {document_rows}"
        )));
    }

    let missing_fts_rows: i64 = connection.query_row(
        "SELECT COUNT(*)
         FROM product_search_documents d
         LEFT JOIN product_search_fts f ON f.rowid=d.rowid
         WHERE f.rowid IS NULL",
        [],
        |row| row.get(0),
    )?;
    let orphan_fts_rows: i64 = connection.query_row(
        "SELECT COUNT(*)
         FROM product_search_fts f
         LEFT JOIN product_search_documents d ON d.rowid=f.rowid
         WHERE d.rowid IS NULL",
        [],
        |row| row.get(0),
    )?;
    if missing_fts_rows != 0 || orphan_fts_rows != 0 {
        return Err(ReferenceRuntimeCapabilityError::ProductSearch(format!(
            "product search rowid alignment mismatch: missing={missing_fts_rows}, orphan={orphan_fts_rows}"
        )));
    }
    Ok(())
}

fn object_kind(
    connection: &Connection,
    name: &str,
) -> Result<Option<String>, ReferenceRuntimeCapabilityError> {
    let mut statement = connection
        .prepare("SELECT type FROM sqlite_master WHERE name=?1 AND type IN ('table','view')")?;
    let mut rows = statement.query([name])?;
    Ok(rows.next()?.map(|row| row.get(0)).transpose()?)
}

fn object_sql(
    connection: &Connection,
    name: &str,
) -> Result<Option<String>, ReferenceRuntimeCapabilityError> {
    let mut statement = connection.prepare(
        "SELECT sql FROM sqlite_master WHERE name=?1 AND type='table' AND sql IS NOT NULL",
    )?;
    let mut rows = statement.query([name])?;
    Ok(rows.next()?.map(|row| row.get(0)).transpose()?)
}

fn table_columns(
    connection: &Connection,
    name: &str,
) -> Result<Vec<String>, ReferenceRuntimeCapabilityError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info([{name}])"))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
}

fn row_count(connection: &Connection, table: &str) -> Result<i64, ReferenceRuntimeCapabilityError> {
    Ok(
        connection.query_row(&format!("SELECT COUNT(*) FROM [{table}]"), [], |row| {
            row.get(0)
        })?,
    )
}
