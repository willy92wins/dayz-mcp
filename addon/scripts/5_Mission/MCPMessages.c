const string MCP_BRIDGE_VERSION = "10";
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

// Command args. A key absent from the command JSON reaches the handler as 0 or
// false, not as the constructor value (fb-20260930-065425-8779: an object_anim
// read wrote phase 0 and a partial world_weather_set zeroed the other levels).
// So a value that must be left alone when absent travels with a bool <field>_set,
// true beside the value. An absent flag reads false and the value is not read.
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
	string root;
	bool bubble;
	string text;
	int button;
	int dik;
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
	// Presence of the four values above (see the class comment).
	bool time_multiplier_set;
	bool overcast_set;
	bool rain_set;
	bool fog_set;
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
	// F3.4 object_anim. phase_set true writes phase; false (absent) only reads.
	string source;
	float phase;
	bool phase_set;
	// F3.5 inventory_give / b256 inventory_attach
	string classname;
	string dest;
	string slot;
	// F3.6 object_inspect — memory-point / bounding_center names
	ref array<string> want;
	// anim_timeline: animation source names read from the item in hands.
	// Up to 8, printable ASCII, 1..64 chars each; empty is valid.
	ref array<string> sources;
	// Optional player identity (GetPlainId). Empty = first human / broadcast.
	string uid;
	// input_describe, and input_trigger with trigger_kind input: UAInput name.
	// Empty on every other command.
	string name;
	// action_use: ActionBase typename (Type().ToString()), e.g. LFPG_ActionOpenBTCAtm.
	string action;
	// action_use_target: hands or self. action_use: empty or world.
	string target;
	// action_use_door. Enforce ints default to 0, and 0 is a valid door, so
	// the command name, not this field, turns door mode on.
	int door_index;
	// action_use_component. 0 is a valid component, so the command name, not
	// this field, turns component mode on. cursor_pos is the world-space
	// point the action measures; absent JSON leaves the array null.
	int component_index;
	ref array<float> cursor_pos;
	// ui_dialog (wire v1.1). title is reused above. kind is this class only.
	string kind;
	string message;
	float timeout_s;
	ref array<ref MCPDialogField> fields;

	// F3.7 infected_drive - heading in DEGREES (bridge converts to radians).
	// Unless mode is release, both flags must be true (see the class comment).
	float heading;
	float speed;
	bool heading_set;
	bool speed_set;
	// weapon_raise. False lowers at once. Other commands leave this false.
	bool raised;
	// weapon_aim. Absolute value capped at MCPWeaponControl.AIM_CHANGE_ABS_MAX.
	float dx;
	float dy;
	// input_trigger (e1ae). trigger_kind is key or input, trigger_edge is click,
	// hold, press or release, trigger_entry is game or mission (key only). dik
	// (key) and name (input) are reused. hold_s travels only with the hold edge
	// and hold_ttl_s only with the press edge; an absent key reads 0, which the
	// bridge refuses (see the class comment).
	string trigger_kind;
	string trigger_edge;
	string trigger_entry;
	float hold_s;
	// world_spawn economy lifetime override in seconds (fb-20260930-080543-bd28).
	// lifetime_s_set true sets it on the spawned entity; false, which is what an
	// absent key arrives as, leaves the economy lifetime alone (see the class comment).
	float lifetime_s;
	bool lifetime_s_set;
	// player_move (c1cb). mode is hold, press or release (phase is object_anim's
	// float). speed (1 walk, 2 jog, 3 sprint), heading (compass degrees, 0 north),
	// to, radius (the arrive radius), hold_s and hold_ttl_s are reused without
	// their _set flags: each phase and direction travels as one exact shape and
	// the bridge refuses the 0 an absent key reads as. direction is angle,
	// heading or to, and angle_deg the relative angle of direction angle.
	string direction;
	float angle_deg;
	// player_heal: true also fills water and energy to their maximum. The tool
	// always sends it; an absent key reads false, the heal without the refill.
	bool full;
	// player_godmode: the state to set, true for godmode on. The tool always
	// sends it; an absent key reads false, which is vanilla damage.
	bool godmode;

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
		sources = new array<string>();
		fields = new array<ref MCPDialogField>();
		component = -1;
		dik = -1;
		limit = 64;
		sample_hz = 20;
		max_samples = 4096;
		hold_ttl_s = 0.0;
		// Second guard only: every reader tests the _set flag first, and the server's
		// IsFiniteFloat refuses float.MAX if a flag ever arrives without its value.
		time_multiplier = MCP_ARG_FLOAT_UNSET;
		overcast = MCP_ARG_FLOAT_UNSET;
		rain = MCP_ARG_FLOAT_UNSET;
		fog = MCP_ARG_FLOAT_UNSET;
		phase = MCP_ARG_FLOAT_UNSET;
		timeout_s = 0.0;
		heading = MCP_ARG_FLOAT_UNSET;
		speed = MCP_ARG_FLOAT_UNSET;
		lifetime_s = MCP_ARG_FLOAT_UNSET;
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

// godmode, here and in MCPAllPlayer, is MCPGodmode.IsOn: GetAllowDamage() false
// on the server's body (MCP_PlayerCare.c).
class MCPPlayerState
{
	string name;
	ref array<float> pos;
	bool godmode;

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
	bool godmode;

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
	string view;
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
	bool has_cargo;
	ref array<float> pos;
	float distance;

	void MCPEntityHit()
	{
		pos = new array<float>();
	}
};

// weapon_state muzzle row. magazine_ammo is 0 when no magazine is attached.
class MCPWeaponMuzzleState
{
	int index;
	bool chamber_empty;
	bool chamber_fired_out;
	bool magazine_present;
	int magazine_ammo;
	int internal_cartridges;
};

// weapon_state payload. shots is the server EEFired tally (MCP_Weapon.c).
class MCPWeaponState
{
	int muzzle_index;
	int mode_index;
	string mode_name;
	bool jammed;
	int shots;
	ref array<ref MCPWeaponMuzzleState> muzzles;

	void MCPWeaponState()
	{
		muzzle_index = -1;
		mode_index = -1;
		mode_name = "";
		muzzles = new array<ref MCPWeaponMuzzleState>();
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

class MCPUiRequestEcho
{
	string requested_path;
	string requested_root;
	string requested_text;
	string matched_path;
};

// One event of a ui_click handler walk; mode="complete" reports three of them.
// handler is the class that consumed it, else the first one that received it,
// else "" when the walk found no handler. received counts the handlers that got
// it: one at most without bubble.
class MCPUiClickPhase
{
	string phase;
	string handler;
	bool consumed;
	int received;
};

// ui_click mode="complete" read-back. x and y are the rounded centre of the
// target's screen box, the point every phase got; bubble echoes the request.
// phases holds down, up and click in the order they ran.
class MCPUiClickSequence
{
	int x;
	int y;
	bool bubble;
	ref array<ref MCPUiClickPhase> phases;

	void MCPUiClickSequence()
	{
		phases = new array<ref MCPUiClickPhase>();
	}
};

class MCPInventoryAttachReceipt
{
	string dest;
	string slot;
};

// input_describe: one key of the selected alternative. index is BindKeyCount's index.
class MCPInputKey
{
	int index;
	int key_code;
	int device;
};

// input_describe probe. Raw UAInput / UAInputAPI values for one non-null
// GetInputByName hit, including the shared placeholder whose index is -1.
// Published so a caller can see why exists is false (ficha 4f50). Not a verdict.
class MCPInputProbe
{
	int input_id;
	int name_hash;
	int name_string_hash;
	bool by_id_found;
	bool by_id_same_hash;
	bool in_active_inputs;
};

// input_describe payload. exists is true only when GetInputByName returns non-null
// and input.ID() >= 0 (uainput.c:25, the input index). In 1.29 an unknown name
// returns a shared placeholder whose index is -1, so exists is false and
// binding_count, locked, conflict_count and keys stay at their defaults.
// probe is published on the non-null path,
// including that placeholder, so a caller can see why exists is false. probe is
// left unset when GetInputByName returns null, so none of its fields are present.
// binding_count, locked, conflict_count and keys are meaningful only when
// exists is true.
class MCPInputDescribe
{
	bool exists;
	int binding_count;
	bool locked;
	int conflict_count;
	ref array<ref MCPInputKey> keys;
	ref MCPInputProbe probe;

	void MCPInputDescribe()
	{
		keys = new array<ref MCPInputKey>();
	}
};

// object_doors: one Building door index. Flags are the engine door predicates.
class MCPDoorState
{
	int index;
	bool open;
	bool opening;
	bool opening_ajar;
	bool opened;
	bool ajar;
	bool closing;
	bool closed;
	bool locked;
	// World position from Building.GetDoorSoundPos for this door index.
	ref array<float> pos;

	void MCPDoorState()
	{
		pos = new array<float>();
	}
};

// Read-back for weapon_raise, weapon_aim, weapon_fire and weapon_sights.
// Angles are the raw vanilla numbers. GetBaseAimingAngleLR/UD and the aim
// overrides do not name a unit. aim_change_* are GetAimChange components
// (human.c:31 documents that vector as radians) in index order.
class MCPWeaponAction
{
	string verb;
	bool raised;
	bool input_raised;
	float expires_at;
	float hold_ttl_s;
	float aim_lr_before;
	float aim_ud_before;
	float aim_lr_after;
	float aim_ud_after;
	float aim_change_0;
	float aim_change_1;
	float aim_change_2;
	bool accepted;
	string reason;
	bool ironsights;
	bool optics;
	string mode;
};

// object_doors payload. door_count is GetDoorCount. doors is empty when the
// count is outside the read cap (the result error names that refusal).
class MCPBuildingDoors
{
	int door_count;
	ref array<ref MCPDoorState> doors;

	void MCPBuildingDoors()
	{
		doors = new array<ref MCPDoorState>();
	}
};

// vehicle_door payload. phase and state are read before any write; phase_reply
// and state_reply in the same tick after it. state and state_reply are
// GetCarDoorsState as open, closed or missing. slot is the door part's
// attachment slot (on door_missing, the empty crew-door slot); pos is the world
// position of selection on the door part. component_index stays -1 and
// phase_requested -1.0 unless set. Primitives only: no entity reference.
class MCPVehicleDoor
{
	string source;
	string mode;
	string slot;
	string door_type;
	string selection;
	int component_index;
	float phase;
	string state;
	bool written;
	float phase_requested;
	float phase_reply;
	string state_reply;
	bool is_authority_owner;
	float tick_time_s;
	ref array<float> pos;

	void MCPVehicleDoor()
	{
		component_index = -1;
		phase_requested = -1.0;
		pos = new array<float>();
	}
};

// input_trigger payload (e1ae part 2). kind, entry, phase and dik or name echo
// the request. delivered_press and delivered_release say the bridge called
// OnKeyPress and OnKeyRelease; the handlers return nothing, so neither says a
// handler consumed the key. press_tick and release_tick count client bridge
// OnTick calls; release_due_s (the scheduled release of a hold or press) and
// tick_time_s (the dispatch) are client GetTickTime seconds. released_by is
// phase, ttl, restore, player_changed or shutdown. menu_open is UIManager.GetMenu
// at dispatch. kind input fills input_id, exists, locked, in_active_inputs and
// reason and delivers nothing. -1 marks an unset dik, id, tick or time.
// Primitives only: no entity reference.
class MCPInputTrigger
{
	string kind;
	string entry;
	string phase;
	int dik;
	string name;
	int input_id;
	bool delivered_press;
	bool delivered_release;
	int press_tick;
	int release_tick;
	float release_due_s;
	float tick_time_s;
	string released_by;
	bool menu_open;
	bool exists;
	bool locked;
	bool in_active_inputs;
	string reason;

	void MCPInputTrigger()
	{
		dik = -1;
		input_id = -1;
		press_tick = -1;
		release_tick = -1;
		release_due_s = -1.0;
		tick_time_s = -1.0;
	}
};

// player_move payload (c1cb). phase echoes the request. speed is walk, jog or
// sprint and speed_value the OverrideMovementSpeed value (1, 2, 3). direction is
// angle, heading or to, with angle_deg, heading_deg, or to and arrive_radius_m
// (the tool keeps only the fields of direction). move_id is the owner's id of
// the move, the one the server's script log names. start_pos and end_pos are
// the owner's PhysicsGetPositionWS at the start and at the release; distance_m
// is the horizontal distance between them. arrived means the move ended inside
// arrive_radius_m. released_by is hold, phase, ttl, arrived, restore,
// player_changed or shutdown. start_tick and release_tick count client bridge
// OnTick calls (the counter tick_dispatch and player_trace's tick report);
// start_tick_time_s, release_tick_time_s and release_due_s (the scheduled end)
// are client GetTickTime seconds, player_trace's monotonic_s. server_request is
// sent or unavailable. applied_angle_deg is the last angle passed to
// OverrideMovementAngle on the owner and command_ticks counts the owner's
// CommandHandler ticks that applied the overrides. -1 marks an unset id, tick,
// time or distance. Primitives only: no entity reference.
class MCPPlayerMove
{
	string phase;
	string speed;
	float speed_value;
	string direction;
	float angle_deg;
	float heading_deg;
	ref array<float> to;
	float arrive_radius_m;
	int move_id;
	ref array<float> start_pos;
	ref array<float> end_pos;
	float distance_m;
	bool arrived;
	string released_by;
	int start_tick;
	int release_tick;
	float start_tick_time_s;
	float release_tick_time_s;
	float release_due_s;
	string server_request;
	float applied_angle_deg;
	int command_ticks;

	void MCPPlayerMove()
	{
		to = new array<float>();
		start_pos = new array<float>();
		end_pos = new array<float>();
		move_id = -1;
		distance_m = -1.0;
		start_tick = -1;
		release_tick = -1;
		start_tick_time_s = -1.0;
		release_tick_time_s = -1.0;
		release_due_s = -1.0;
	}
};

// world_spawn lifetime read-back (fb-20260930-080543-bd28), assigned only when
// the command carried lifetime_s. remaining_s is GetLifetime() and max_s is
// GetLifetimeMax() (entityai.c:3377-3387), both read when the reply is built.
// Primitives only: no entity reference.
class MCPSpawnLifetime
{
	float remaining_s;
	float max_s;
};

// world_time_get read: the server's World.GetDate (world.c:33) when the reply
// is built, as the engine reports it. The tool carries a minute of 60 into the
// hour, as world_time_set does for its applied echo. World has no time
// multiplier getter, so none is carried. Primitives only: no entity reference.
class MCPWorldTime
{
	int year;
	int month;
	int day;
	int hour;
	int minute;
};

// player_heal vitals of the server's PlayerBase, read before and after the heal.
// health, blood and shock are GetHealth01 of the global zone (object.c:997), 1.0
// at the maximum. broken_legs is GetBrokenLegs() other than NO_BROKEN_LEGS
// (playerbase.c:3696-3699), a splinted leg included. bleeding_sources is
// GetBleedingSourceCount() (playerbase.c:7585-7588). water and energy are the
// stats' values, 0 to 5000 (playerstatspco.c:297-298). Primitives only.
class MCPPlayerVitals
{
	float health;
	float blood;
	float shock;
	bool broken_legs;
	bool unconscious;
	int bleeding_sources;
	float water;
	float energy;
};

// player_heal reply. full echoes the request and in_vehicle is IsInVehicle()
// before the heal (dayzplayerimplement.c:465-468): the vehicle command or a
// Transport parent. When a worn splint came off with the healed legs,
// splint_returned_to names where a new Splint item was found after the heal,
// inventory (the inventory or the hands) or ground, and splint_returned is then
// true; it is none, with splint_returned false, when no new Splint was found
// there: vanilla deletes the applied splint even when it cannot place the item.
// Both are false and "" when no splint came off
// (PlayerBase.MCPHealServer, MCP_PlayerCare.c). Primitives only: no entity reference.
class MCPPlayerHeal
{
	bool full;
	bool in_vehicle;
	bool splint_returned;
	string splint_returned_to;
	ref MCPPlayerVitals before;
	ref MCPPlayerVitals after;

	void MCPPlayerHeal()
	{
		before = new MCPPlayerVitals();
		after = new MCPPlayerVitals();
	}
};

// player_godmode reply: MCPGodmode.IsOn of the server's body before and after
// the switch, true for godmode on. Primitives only: no entity reference.
class MCPPlayerGodmode
{
	bool before;
	bool after;
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
	// F3.5 inventory_give / b256 inventory_attach item identity.
	string classname;
	ref MCPInventoryAttachReceipt inventory_attach;
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
	ref MCPUiRequestEcho ui_request;
	bool clicked;
	string handler;
	int user_id;
	// key_press: delivered means Mission.OnKeyPress was invoked, not consumed.
	bool delivered;
	int dik;
	// player_respawn: the vanilla request sequence ran; completion is asynchronous.
	bool requested;
	// action_use: started means local dispatch only; server ack is not awaited.
	string action;
	string target;
	float distance;
	bool started;
	// action_use_door / action_use_component. Same default-0 rule as
	// MCPArgs.door_index: 0 is a valid door and a valid component, so
	// the command name, not these fields, is what turns the mode on.
	// door_index is filled only by action_use_door. component_index is
	// filled by action_use_door and action_use_component.
	int door_index;
	int component_index;
	// ui_dialog nested payload. Unassigned on other commands.
	ref MCPDialogResult dialog;
	// hands_take: accepted means the request passed server checks. confirmed
	// stays false; the predictive take finishes later. weapon_state is the read.
	bool accepted;
	bool confirmed;
	ref MCPWeaponState weapon_state;
	// input_describe. Unassigned on other commands.
	ref MCPInputDescribe input_describe;
	// object_doors. Unassigned on other commands.
	ref MCPBuildingDoors building_doors;
	// weapon_raise / weapon_aim / weapon_fire / weapon_sights read-back.
	// Unassigned on other commands.
	ref MCPWeaponAction weapon_action;
	// anim_timeline header and paged samples. Unassigned on other commands.
	ref MCPAnimTimelineRead timeline;
	// vehicle_door read-back, also on door_missing. Unassigned on other commands.
	ref MCPVehicleDoor vehicle_door;
	// world_spawn lifetime read-back. Unassigned unless the command carried lifetime_s.
	ref MCPSpawnLifetime lifetime;
	// input_trigger reply, also on its refusals. Unassigned on other commands.
	ref MCPInputTrigger input_trigger;
	// world_time_get read. Unassigned on other commands.
	ref MCPWorldTime world_time;
	// ui_click mode="complete" read-back. Unassigned in direct mode and on other commands.
	ref MCPUiClickSequence click_sequence;
	// player_heal reply. Unassigned on its refusals and on other commands.
	ref MCPPlayerHeal player_heal;
	// player_godmode reply. Unassigned on its refusals and on other commands.
	ref MCPPlayerGodmode player_godmode;
	// player_move reply, also on its refusals once the phase is known.
	// Unassigned on other commands.
	ref MCPPlayerMove player_move;
	// player_trace header and paged samples. Unassigned on other commands.
	ref MCPPlayerTraceRead player_trace;
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
	int generation;
	int sim_seen;
	ref MCPWeaponAction weapon_action;
	// input_trigger click or hold: the reply posted after the release.
	ref MCPInputTrigger input_trigger;
	// player_move hold: the reply posted after the release.
	ref MCPPlayerMove player_move;
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
