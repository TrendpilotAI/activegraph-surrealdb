-- ActiveGraph SurrealDB provider schema, version 1.
-- All logical identifiers are retained as fields; record IDs are opaque hashes.

DEFINE TABLE IF NOT EXISTS ag_schema SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS version ON TABLE ag_schema TYPE int;
DEFINE FIELD IF NOT EXISTS installed_at ON TABLE ag_schema TYPE string;

DEFINE TABLE IF NOT EXISTS ag_run SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_run TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_run TYPE string;
DEFINE FIELD IF NOT EXISTS parent_run_id ON TABLE ag_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS forked_at_event_id ON TABLE ag_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS label ON TABLE ag_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON TABLE ag_run TYPE string;
DEFINE FIELD IF NOT EXISTS goal ON TABLE ag_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS frame_id ON TABLE ag_run TYPE option<string>;
DEFINE FIELD IF NOT EXISTS next_seq ON TABLE ag_run TYPE int;
DEFINE FIELD IF NOT EXISTS head_hash ON TABLE ag_run TYPE option<string>;
DEFINE INDEX IF NOT EXISTS ag_run_scope_run ON TABLE ag_run FIELDS scope, run_id UNIQUE;

DEFINE TABLE IF NOT EXISTS ag_event SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS event_id ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS seq ON TABLE ag_event TYPE int;
DEFINE FIELD IF NOT EXISTS event_type ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS payload_json ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS actor ON TABLE ag_event TYPE option<string>;
DEFINE FIELD IF NOT EXISTS frame_id ON TABLE ag_event TYPE option<string>;
DEFINE FIELD IF NOT EXISTS caused_by ON TABLE ag_event TYPE option<string>;
DEFINE FIELD IF NOT EXISTS timestamp ON TABLE ag_event TYPE string;
DEFINE FIELD IF NOT EXISTS previous_hash ON TABLE ag_event TYPE option<string>;
DEFINE FIELD IF NOT EXISTS hash ON TABLE ag_event TYPE string;
DEFINE INDEX IF NOT EXISTS ag_event_scope_run_id ON TABLE ag_event FIELDS scope, run_id, event_id UNIQUE;
DEFINE INDEX IF NOT EXISTS ag_event_scope_run_seq ON TABLE ag_event FIELDS scope, run_id, seq UNIQUE;

DEFINE TABLE IF NOT EXISTS ag_projection SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_projection TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_projection TYPE string;
DEFINE FIELD IF NOT EXISTS status ON TABLE ag_projection TYPE string;
DEFINE FIELD IF NOT EXISTS error ON TABLE ag_projection TYPE option<string>;
DEFINE FIELD IF NOT EXISTS updated_at ON TABLE ag_projection TYPE string;
DEFINE INDEX IF NOT EXISTS ag_projection_scope_run ON TABLE ag_projection FIELDS scope, run_id UNIQUE;

DEFINE TABLE IF NOT EXISTS ag_vertex SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_vertex TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_vertex TYPE string;
DEFINE FIELD IF NOT EXISTS object_id ON TABLE ag_vertex TYPE string;
DEFINE FIELD IF NOT EXISTS object_present ON TABLE ag_vertex TYPE bool;
DEFINE FIELD IF NOT EXISTS object_type ON TABLE ag_vertex TYPE option<string>;
DEFINE FIELD IF NOT EXISTS data_json ON TABLE ag_vertex TYPE option<string>;
DEFINE FIELD IF NOT EXISTS version ON TABLE ag_vertex TYPE option<int>;
DEFINE FIELD IF NOT EXISTS provenance_json ON TABLE ag_vertex TYPE option<string>;
DEFINE INDEX IF NOT EXISTS ag_vertex_scope_run_id ON TABLE ag_vertex FIELDS scope, run_id, object_id UNIQUE;

DEFINE TABLE IF NOT EXISTS ag_relation SCHEMAFULL TYPE RELATION IN ag_vertex OUT ag_vertex;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS relation_id ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS source ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS target ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS relation_type ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS data_json ON TABLE ag_relation TYPE string;
DEFINE FIELD IF NOT EXISTS provenance_json ON TABLE ag_relation TYPE string;
DEFINE INDEX IF NOT EXISTS ag_relation_scope_run_id ON TABLE ag_relation FIELDS scope, run_id, relation_id UNIQUE;

DEFINE TABLE IF NOT EXISTS ag_patch SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS scope ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS run_id ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS patch_id ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS target ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS op ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS value_json ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS expected_version ON TABLE ag_patch TYPE int;
DEFINE FIELD IF NOT EXISTS proposed_by ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS rationale ON TABLE ag_patch TYPE option<string>;
DEFINE FIELD IF NOT EXISTS evidence_json ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS status ON TABLE ag_patch TYPE string;
DEFINE FIELD IF NOT EXISTS rejection_reason ON TABLE ag_patch TYPE option<string>;
DEFINE FIELD IF NOT EXISTS provenance_json ON TABLE ag_patch TYPE string;
DEFINE INDEX IF NOT EXISTS ag_patch_scope_run_id ON TABLE ag_patch FIELDS scope, run_id, patch_id UNIQUE;
