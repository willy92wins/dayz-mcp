const string MCP_BRIDGE_VERSION = "9";
const float MCP_ARG_FLOAT_UNSET = float.MAX;
const int MCP_FIXTURE_SEQ_UNSET = -2147483647;

class MCPConfig
{
	string url;
	string key;
	float pollHz;
	string instance;
};

// ui_dialog wire field. Key is default_text; default is reserved in Enforce.
class MCPDialogField
{
	string id;
	string label;
	bool required;
	string default_text;
};

class MCPDialogValue
{
	string id;
	string value;
};

class MCPDialogResult
{
	string state;
	string dismissed_by;
	string choice;
	ref array<ref MCPDialogValue> values;
	string reason;
	float elapsed_s;

	void MCPDialogResult()
	{
		values = new array<ref MCPDialogValue>();
	}
};

class MCPArgs
{
	string type;
	ref array<float> pos;
	ref array<float> from;
	ref array<float> to;
	int flags;
	int rotation;
	int seat;
	int component;
	float throttle;
	float steer;
	float brake;
	float handbrake;
	float hold_ttl_s;
	float duration;
	float radius;
	string method;
	string intersect;
	string ignore;
	string mode;
	string trace_id;
	int cursor;
	int limit;
	int sample_hz;
	int max_samples;
	string path;
	string text;
	int button;
	int max_lines;
	int camera;
	float fov;
	ref array<float> cam_pos;
	ref array<float> cam_orientation;
	ref array<float> cam_matrix;
	string cam_mode;
	ref array<float> look_at;
	int settle_ticks;
	string capture_scale;
	int max_tokens;
	int year;
	int month;
	int day;
	int hour;
	int minute;
	float time_multiplier;
	float overcast;
	float rain;
	float fog;
	float time;
	float min_duration;
	string expr;
	string main_fn;
	int object_id;
	float show_time;
	string title;
	string detail;
	string icon;
	// F3.1 surface_query
	float x;
	float z;
	// F3.4 object_anim (phase == MCP_ARG_FLOAT_UNSET means read-only)
	string source;
	float phase;
	// F3.5 inventory_give
	string classname;
	string dest;
	// F3.6 object_inspect — memory-point / bounding_center names
	ref array<string> want;
	// Optional player identity (GetPlainId). Empty = first human / broadcast.
	string uid;
	// action_use: ActionBase typename (Type().ToString()), e.g. LFPG_ActionOpenBTCAtm.
	string action;
	// ui_dialog (wire v1.1). title is reused above. kind is this class only.
	string kind;
	string message;
	float timeout_s;
	ref array<ref MCPDialogField> fields;

	// F3.7 infected_drive - heading in DEGREES (bridge converts to radians).
	float heading;
	float speed;

	void MCPArgs()
	{
		pos = new array<float>();
		from = new array<float>();
		to = new array<float>();
		cam_pos = new array<float>();
		cam_orientation = new array<float>();
		cam_matrix = new array<float>();
		look_at = new array<float>();
		want = new array<string>();
		fields = new array<ref MCPDialogField>();
		component = -1;
		limit = 64;
		sample_hz = 20;
		max_samples = 4096;
		hold_ttl_s = 0.0;
		time_multiplier = MCP_ARG_FLOAT_UNSET;
		overcast = MCP_ARG_FLOAT_UNSET;
		rain = MCP_ARG_FLOAT_UNSET;
		fog = MCP_ARG_FLOAT_UNSET;
		phase = MCP_ARG_FLOAT_UNSET;
		timeout_s = 0.0;
		heading = MCP_ARG_FLOAT_UNSET;
		speed = MCP_ARG_FLOAT_UNSET;
	}
};

class MCPCommand
{
	int id;
	string cmd;
	ref MCPArgs args;
};

class MCPCommandBatch
{
	ref array<ref MCPCommand> commands;

	void MCPCommandBatch()
	{
		commands = new array<ref MCPCommand>();
	}
};

class MCPPlayerState
{
	string name;
	ref array<float> pos;

	void MCPPlayerState()
	{
		pos = new array<float>();
	}
};

class MCPAllPlayer
{
	string uid;
	ref array<float> pos;
	float health;
	bool in_vehicle;

	void MCPAllPlayer()
	{
		pos = new array<float>();
	}
};

class MCPRaycastHit
{
	bool hit;
	string method;
	ref array<float> pos;
	ref array<float> normal;
	float distance;
	string object_type;
	string object_class;
	string parent_type;
	int component;
	int hier_level;
	string surface_name;
	string surface_type;
	bool entry;
	bool exit;

	void MCPRaycastHit()
	{
		pos = new array<float>();
		normal = new array<float>();
	}
};

class MCPTelemetryFixtureLine
{
	string fixture_id;
	float value;
	int seq;

	void MCPTelemetryFixtureLine()
	{
		Reset();
	}

	void Reset()
	{
		fixture_id = "";
		value = float.MAX;
		seq = MCP_FIXTURE_SEQ_UNSET;
	}
};

class MCPTelemetry
{
	string mode;
	bool found;
	string type;
	string class_name;
	ref array<float> pos;
	ref array<float> orientation;
	ref array<float> direction;
	ref array<float> velocity;
	float health01;
	int attachment_count;
	int cargo_count;
	ref array<string> items;
	ref array<string> attachment_items;
	ref array<string> cargo_items;
	int items_total;
	bool items_truncated;
	ref array<string> declared_slots;
	bool engine_on_server;
	float speedo;
	int wheel_count;
	float fuel_fraction;
	string path;
	int line_count_read;
	ref MCPTelemetryFixtureLine last_valid;
	string parse_error;

	void MCPTelemetry()
	{
		pos = new array<float>();
		orientation = new array<float>();
		direction = new array<float>();
		velocity = new array<float>();
		items = new array<string>();
		attachment_items = new array<string>();
		cargo_items = new array<string>();
		declared_slots = new array<string>();
		last_valid = new MCPTelemetryFixtureLine();
	}
};

class MCPCamera
{
	bool ok;
	string applied_mode;
	ref array<float> pos;
	ref array<float> matrix;
	ref array<float> dir;
	float fov;
	bool interpolation_complete;
	bool viewport_moved;
	string error;

	void MCPCamera()
	{
		pos = new array<float>();
		matrix = new array<float>();
		dir = new array<float>();
	}
};

class MCPApplied
{
	int year;
	int month;
	int day;
	int hour;
	int minute;
	float overcast_actual;
	float rain_actual;
	float fog_actual;
	float overcast_forecast;
	float rain_forecast;
	float fog_forecast;
};

class MCPSeatCondition
{
	int crew_index;
	bool crew_can_get_through;
	bool area_free;
	bool occupied;
	bool reachable;
};

class MCPGetInCondition
{
	bool available;
	bool partial;
	int crew_size;
	int component_crew_index;
	string first_block;
	ref array<ref MCPSeatCondition> per_seat;

	void MCPGetInCondition()
	{
		per_seat = new array<ref MCPSeatCondition>();
	}
};

// F3.6: one memory-point answer. Absent points keep ok:true with exists:false.
class MCPMemoryPoint
{
	string name;
	bool exists;
	ref array<float> pos;

	void MCPMemoryPoint()
	{
		pos = new array<float>();
	}
};

// entities_query row. type is Object.GetType(); classname is Object.ClassName().
class MCPEntityHit
{
	string type;
	string classname;
	ref array<float> pos;
	float distance;

	void MCPEntityHit()
	{
		pos = new array<float>();
	}
};

// F3.6 object_inspect payload. bounding_center is model-local (GetBoundingCenter).
class MCPObjectInspect
{
	string type;
	ref array<float> bounding_center;
	bool has_bounding_center;
	ref array<ref MCPMemoryPoint> memory_points;

	void MCPObjectInspect()
	{
		bounding_center = new array<float>();
		memory_points = new array<ref MCPMemoryPoint>();
		has_bounding_center = false;
	}
};

// One widget in a client UI walk. text_readable is false when the proto has no getter.
class MCPUiNode
{
	string name;
	string type;
	int user_id;
	bool visible;
	bool visible_hierarchy;
	bool disabled;
	bool ignore_pointer;
	int color;
	float screen_x;
	float screen_y;
	float screen_w;
	float screen_h;
	string text;
	bool text_readable;
};

class MCPUiSnapshot
{
	ref array<ref MCPUiNode> nodes;

	void MCPUiSnapshot()
	{
		nodes = new array<ref MCPUiNode>();
	}
};

class MCPResult
{
	int id;
	bool ok;
	string error;
	ref MCPPlayerState state;
	ref array<ref MCPAllPlayer> players;
	ref MCPRaycastHit raycast;
	ref MCPTelemetry telemetry;
	ref MCPCamera camera;
	ref MCPApplied applied;
	ref MCPGetInCondition get_in;
	ref MCPVehicleTraceRead trace;
	string type;
	ref array<float> pos_real;
	bool found;
	bool seated;
	string seat;
	bool sent;
	bool vehicle_fixture_ready;
	bool engine_on_server;
	float speedo_max;
	float pos_delta;
	int gear;
	int net_strategy;
	bool is_owner;
	string owner_identity;
	int net_id_low;
	int net_id_high;
	bool is_authority_owner;
	int object_id;
	int deleted;
	int tick_poll_sent;
	int tick_poll_callback;
	int tick_dispatch;
	// F3.1 surface_query — y is SurfaceGetType's return (surface hit Y + out type).
	float y;
	ref array<float> normal;
	// F3.4 object_anim phase after read or write.
	float phase;
	string source;
	// F3.5 inventory_give classname echoed on success.
	string classname;
	// F3.5: true when dest=hands and hands were occupied — vanilla drops the held
	// item and CallLater-spawns ~500 ms later (SpawnEntityInPlayerInventory returns null).
	bool deferred;
	// F3.6 object_inspect
	ref MCPObjectInspect inspect;
	// entities_query: raw nearby objects, nearest-first, cut at args.limit.
	int count_total;
	ref array<ref MCPEntityHit> entities;
	// Client UI verbs (ui_tree / ui_set_text / ui_click).
	ref MCPUiSnapshot ui;
	bool clicked;
	string handler;
	int user_id;
	// action_use: started means local dispatch only; server ack is not awaited.
	string action;
	float distance;
	bool started;
	// ui_dialog nested payload. Unassigned on other commands.
	ref MCPDialogResult dialog;
};

class MCPJob
{
	int id;
	string kind;
	ref MCPArgs args;
	Object subject;
	Human actor;
	float deadline_s;
	float prep_deadline_s;
	int phase;
	float sample_start_s;
	float sample_s_target;
	bool fixture_attempted;
	bool vehicle_fixture_ready;
	bool engine_on_server;
	float speedo_max;
	float pos_delta;
	int net_strategy;
	bool is_owner;
	string owner_identity;
	int net_id_low;
	int net_id_high;
	bool is_authority_owner;
	bool seat_attempted;
	bool sim_restored;
	vector start_pos;
	string error;
	int tick_poll_sent;
	int tick_poll_callback;
	int tick_dispatch;
	ref MCPDialogResult dialog;
};

class MCPSpawnValidation
{
	bool ok;
	string error;
	vector pos;
	int flags;
	int rotation;
};

class MCPCameraValidation
{
	bool ok;
	string error;
	int mode_id;
	vector pos;
	vector orient;
	vector look_at;
	float fov;
};

class MCPRaycastValidation
{
	bool ok;
	string error;
	vector from;
	vector to;
	int intersect_type;
	float radius;
};

class MCPTelemetryValidation
{
	bool ok;
	string error;
	string mode;
	string type;
	string path;
	vector pos;
	float radius;
	int max_lines;
};
