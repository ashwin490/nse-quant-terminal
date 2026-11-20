When an existing market_data.duckdb is present in the app directory,
startup makes a dated copy here if a copy for that day does not already exist.
This is a local backup, not off-site disaster recovery.
For production use, independently snapshot/check and replicate the DB to persistent storage.
