use rusqlite::{params, Connection};
use serde_json::{json, Value};

pub(crate) fn recent_logs(
    con: &Connection,
    person_id: &str,
    limit: i64,
) -> rusqlite::Result<Vec<Value>> {
    let mut statement = con.prepare(
        "SELECT l.id,l.medication_id,l.person_id,l.status,l.occurred_at,l.note,
                l.product_name_snapshot,l.dosage_text_snapshot,l.created_at,l.dose_instance_id,
                COALESCE(l.product_name_snapshot,m.product_name) AS product_name,
                COALESCE(l.dosage_text_snapshot,m.dosage_text) AS dosage_text,
                r.request_id
         FROM dose_logs l
         JOIN medications m ON m.id=l.medication_id
         LEFT JOIN prn_requests r
           ON r.dose_instance_id=l.dose_instance_id AND r.state='active'
         WHERE l.person_id=? ORDER BY l.occurred_at DESC, l.rowid DESC LIMIT ?",
    )?;
    let rows = statement.query_map(params![person_id, limit], |row| {
        let request_id: Option<String> = row.get(12)?;
        let mut value = json!({
            "id": row.get::<_, String>(0)?,
            "medication_id": row.get::<_, String>(1)?,
            "person_id": row.get::<_, String>(2)?,
            "status": row.get::<_, String>(3)?,
            "occurred_at": row.get::<_, String>(4)?,
            "note": row.get::<_, Option<String>>(5)?,
            "product_name_snapshot": row.get::<_, Option<String>>(6)?,
            "dosage_text_snapshot": row.get::<_, Option<String>>(7)?,
            "created_at": row.get::<_, String>(8)?,
            "dose_instance_id": row.get::<_, Option<String>>(9)?,
            "product_name": row.get::<_, String>(10)?,
            "dosage_text": row.get::<_, Option<String>>(11)?,
        });
        if let Some(request_id) = request_id {
            value
                .as_object_mut()
                .expect("dose log JSON is an object")
                .insert("request_id".to_owned(), Value::String(request_id));
        }
        Ok(value)
    })?;
    rows.collect()
}
