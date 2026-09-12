# Database status

Only three upgrade scripts were present in the supplied project archive. They assume an existing `continio` schema; they do not define all core tables.

The source references tables including `students`, `teachers`, `classes`, `subjects`, `rooms`, `timetable`, `attendance_sessions`, `attendance`, `wave_results`, `continio_accounts` and the Telegram tables. Column definitions, indexes and constraints must match the original application database. A guessed replacement schema has not been added.

To make a fresh installation reproducible, export **structure only** from the working MySQL database and save the reviewed result as `database/schema.sql`. For example, from a local terminal:

```sh
mysqldump --no-data --skip-comments --no-tablespaces -u YOUR_DB_USER -p continio > database/schema.sql
```

This prompts for the password rather than placing it on the command line. Review the export for stored object definitions or account-specific definers before committing it. Do not include student rows, face embeddings, account hashes, Telegram links or attendance data.

If the schema export is from an already-upgraded database, document its version and do not blindly rerun upgrade scripts. See [setup](../docs/SETUP.md) for the intended migration order on a compatible older installation.
