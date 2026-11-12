-- Schema of the single-bar portal (commit 9d521ba), before tenants and businesses. Used by test_tenancy_upgrade.py.
CREATE TABLE app_settings (
	"key" VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	PRIMARY KEY ("key")
);
CREATE TABLE audit_changes (
	id VARCHAR(36) NOT NULL, 
	event_id VARCHAR(36) NOT NULL, 
	position INTEGER NOT NULL, 
	at DATETIME NOT NULL, 
	table_name VARCHAR(64) NOT NULL, 
	row_id VARCHAR(200), 
	op VARCHAR(16) NOT NULL, 
	changes JSON, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_changes_event_id ON audit_changes (event_id);
CREATE INDEX ix_audit_changes_at ON audit_changes (at);
CREATE TABLE locations (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	swype_location_id VARCHAR(64), 
	PRIMARY KEY (id)
);
CREATE TABLE recipes (
	id VARCHAR(36) NOT NULL, 
	swype_code VARCHAR(32) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	ignored BOOLEAN NOT NULL, 
	steps TEXT, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (swype_code)
);
CREATE TABLE swype_menu_items (
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	custom_name VARCHAR(200), 
	category VARCHAR(120), 
	source VARCHAR(16) NOT NULL, 
	is_active BOOLEAN DEFAULT 1 NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (code)
);
CREATE TABLE units (
	code VARCHAR(16) NOT NULL, 
	dimension VARCHAR(16) NOT NULL, 
	to_base NUMERIC(14, 6) NOT NULL, 
	PRIMARY KEY (code)
);
CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	pin_hash VARCHAR(255), 
	is_admin BOOLEAN NOT NULL, 
	permissions JSON NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	session_version INTEGER NOT NULL, 
	last_login_at DATETIME, 
	password_changed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
CREATE TABLE audit_events (
	id VARCHAR(36) NOT NULL, 
	at DATETIME NOT NULL, 
	user_id VARCHAR(36), 
	username VARCHAR(64), 
	seq INTEGER, 
	action VARCHAR(32) NOT NULL, 
	area VARCHAR(32), 
	method VARCHAR(8), 
	route VARCHAR(200), 
	path VARCHAR(500), 
	status INTEGER, 
	ip VARCHAR(64), 
	detail JSON, 
	prev_hash VARCHAR(64), 
	hash VARCHAR(64), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	UNIQUE (seq)
);
CREATE INDEX ix_audit_at ON audit_events (at);
CREATE INDEX ix_audit_user_at ON audit_events (user_id, at);
CREATE TABLE count_sessions (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36) NOT NULL, 
	counted_at DATETIME NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	external_ref VARCHAR(200), 
	note TEXT, 
	posted_at DATETIME, 
	superseded_by VARCHAR(36), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	FOREIGN KEY(superseded_by) REFERENCES count_sessions (id)
);
CREATE INDEX ix_count_sessions_loc_at ON count_sessions (location_id, counted_at);
CREATE TABLE deliveries (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36) NOT NULL, 
	store VARCHAR(200), 
	store_address VARCHAR(300), 
	occurred_at DATETIME, 
	receipt_no VARCHAR(100), 
	subtotal NUMERIC(14, 2), 
	tax NUMERIC(14, 2), 
	total NUMERIC(14, 2), 
	status VARCHAR(16) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	note TEXT, 
	read_error TEXT, 
	extracted JSON, 
	posted_at DATETIME, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id)
);
CREATE TABLE import_batches (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36), 
	source VARCHAR(16) NOT NULL, 
	dataset VARCHAR(16) NOT NULL, 
	file_name VARCHAR(255), 
	file_sha256 VARCHAR(64), 
	period_from DATETIME, 
	period_to DATETIME, 
	rows_received INTEGER NOT NULL, 
	rows_inserted INTEGER NOT NULL, 
	rows_duplicate INTEGER NOT NULL, 
	rows_error INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	error_log JSON, 
	imported_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id)
);
CREATE TABLE inventory_items (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36), 
	sku VARCHAR(64) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	category VARCHAR(64), 
	stock_unit VARCHAR(16) NOT NULL, 
	pack_name VARCHAR(32), 
	pack_size NUMERIC(14, 4) NOT NULL, 
	case_size NUMERIC(14, 4) NOT NULL, 
	pack_cost NUMERIC(14, 4), 
	yield_to_unit VARCHAR(16), 
	yield_qty NUMERIC(14, 4), 
	par_level NUMERIC(14, 4), 
	is_active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	UNIQUE (sku), 
	FOREIGN KEY(stock_unit) REFERENCES units (code), 
	FOREIGN KEY(yield_to_unit) REFERENCES units (code)
);
CREATE TABLE item_groups (
	name VARCHAR(64) NOT NULL, 
	stock_unit VARCHAR(16) NOT NULL, 
	pack_name VARCHAR(32), 
	pack_size NUMERIC(14, 4) NOT NULL, 
	case_size NUMERIC(14, 4) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (name), 
	FOREIGN KEY(stock_unit) REFERENCES units (code)
);
CREATE TABLE recipe_photos (
	recipe_id VARCHAR(36) NOT NULL, 
	content_type VARCHAR(64) NOT NULL, 
	data BLOB NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (recipe_id), 
	FOREIGN KEY(recipe_id) REFERENCES recipes (id)
);
CREATE TABLE recipe_versions (
	id VARCHAR(36) NOT NULL, 
	recipe_id VARCHAR(36) NOT NULL, 
	effective_from DATE NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_recipe_version_from UNIQUE (recipe_id, effective_from), 
	FOREIGN KEY(recipe_id) REFERENCES recipes (id)
);
CREATE TABLE count_lines (
	session_id VARCHAR(36) NOT NULL, 
	item_id VARCHAR(36) NOT NULL, 
	packs_full NUMERIC(14, 4) NOT NULL, 
	packs_partial NUMERIC(14, 4) NOT NULL, 
	qty NUMERIC(14, 4) NOT NULL, 
	note TEXT, 
	PRIMARY KEY (session_id, item_id), 
	FOREIGN KEY(session_id) REFERENCES count_sessions (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id)
);
CREATE TABLE data_issues (
	id VARCHAR(36) NOT NULL, 
	code VARCHAR(48) NOT NULL, 
	entity VARCHAR(32), 
	entity_id VARCHAR(64), 
	detail JSON, 
	import_batch_id VARCHAR(36), 
	resolved BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(import_batch_id) REFERENCES import_batches (id)
);
CREATE TABLE delivery_lines (
	id VARCHAR(36) NOT NULL, 
	delivery_id VARCHAR(36) NOT NULL, 
	position INTEGER NOT NULL, 
	text VARCHAR(300) NOT NULL, 
	receipt_sku VARCHAR(64), 
	receipt_qty NUMERIC(14, 4) NOT NULL, 
	line_total NUMERIC(14, 2), 
	item_id VARCHAR(36), 
	packs NUMERIC(14, 4), 
	skip BOOLEAN NOT NULL, 
	matched_by VARCHAR(16), 
	PRIMARY KEY (id), 
	FOREIGN KEY(delivery_id) REFERENCES deliveries (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id)
);
CREATE TABLE delivery_receipts (
	delivery_id VARCHAR(36) NOT NULL, 
	content_type VARCHAR(64) NOT NULL, 
	file_name VARCHAR(255), 
	data BLOB NOT NULL, 
	uploaded_at DATETIME NOT NULL, 
	PRIMARY KEY (delivery_id), 
	FOREIGN KEY(delivery_id) REFERENCES deliveries (id)
);
CREATE TABLE inventory_item_costs (
	id VARCHAR(36) NOT NULL, 
	item_id VARCHAR(36) NOT NULL, 
	effective_from DATETIME NOT NULL, 
	pack_cost NUMERIC(14, 4) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id)
);
CREATE TABLE receipt_aliases (
	id VARCHAR(36) NOT NULL, 
	store_key VARCHAR(200) NOT NULL, 
	text_key VARCHAR(300) NOT NULL, 
	sku VARCHAR(64), 
	item_id VARCHAR(36), 
	packs_per_unit NUMERIC(14, 4) NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id)
);
CREATE UNIQUE INDEX uq_receipt_alias_line ON receipt_aliases (store_key, text_key, coalesce(sku, ''));
CREATE TABLE recipe_lines (
	id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	item_id VARCHAR(36) NOT NULL, 
	qty NUMERIC(14, 4) NOT NULL, 
	unit VARCHAR(16) NOT NULL, 
	position INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(version_id) REFERENCES recipe_versions (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id), 
	FOREIGN KEY(unit) REFERENCES units (code)
);
CREATE TABLE stock_movements (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36) NOT NULL, 
	item_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	qty NUMERIC(14, 4) NOT NULL, 
	packs NUMERIC(14, 4), 
	pack_cost NUMERIC(14, 4), 
	occurred_at DATETIME NOT NULL, 
	external_ref VARCHAR(200) NOT NULL, 
	source VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	note TEXT, 
	import_batch_id VARCHAR(36), 
	delivery_id VARCHAR(36), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_movement_ref UNIQUE (location_id, item_id, kind, external_ref), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	FOREIGN KEY(item_id) REFERENCES inventory_items (id), 
	FOREIGN KEY(import_batch_id) REFERENCES import_batches (id), 
	FOREIGN KEY(delivery_id) REFERENCES deliveries (id)
);
CREATE INDEX ix_movements_item_at ON stock_movements (item_id, occurred_at);
CREATE TABLE swype_item_sales (
	id VARCHAR(36) NOT NULL, 
	location_id VARCHAR(36) NOT NULL, 
	period_from DATE NOT NULL, 
	period_to DATE NOT NULL, 
	department VARCHAR(64) NOT NULL, 
	category VARCHAR(120) NOT NULL, 
	item_code VARCHAR(32) NOT NULL, 
	description VARCHAR(200) NOT NULL, 
	qty NUMERIC(14, 4) NOT NULL, 
	combo_qty NUMERIC(14, 4) NOT NULL, 
	promo_qty NUMERIC(14, 4) NOT NULL, 
	free_qty NUMERIC(14, 4) NOT NULL, 
	total_qty NUMERIC(14, 4) NOT NULL, 
	amount NUMERIC(14, 2) NOT NULL, 
	net_amount NUMERIC(14, 2) NOT NULL, 
	import_batch_id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_swype_item_sales UNIQUE (location_id, period_from, period_to, item_code), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	FOREIGN KEY(import_batch_id) REFERENCES import_batches (id)
);
CREATE INDEX ix_swype_item_sales_period ON swype_item_sales (location_id, period_from);
