"""Shared finalize policy for records staged by an outer transaction."""


def finalize_staged_record(db, record, *, defer_commit: bool) -> None:
    if defer_commit:
        db.flush()
        return
    db.commit()
    db.refresh(record)
