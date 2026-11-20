// Server-side per-player kinematic sampler. Lives in 4_World so View can build
// MCPPlayersKinematicsRead without depending on 5_Mission.
//
// MissionServer.OnUpdate calls MCPPlayersKinematics.Tick() on every server
// frame. When idle, Tick returns before touching the engine. While active, it
// runs passes at 5..10 Hz against GetGame().GetTime() (integer milliseconds)
// and samples every connected living PlayerBase with an identity (optionally
// filtered by an explicit list of plain uids). Dead bodies are skipped so a
// respawn appears as a subject_absent gap instead of an in-cadence jump.
//
// The sampler never runs raycasts and never writes world or player state. Each
// pass is timed with TickCount; if a pass exceeds PASS_BUDGET_TICKS (0.5 ms at
// 10 000 ticks/ms), the next due pass is skipped and counted in budget_skips.

//! Per-uid previous sample record kept while a trace exists.
class MCPPlayersKinematicsPrev
{
	int tick_ms;
	vector pos;
	int pass_index;
};

//! One sampled kinematic row for a single living player at one server pass.
//! JsonSerializer always writes every member; unmeasured optional quantities
//! travel with their *_available flag set to false and are stripped in Python.
class MCPPlayersKinematicsRow
{
	string uid;
	int tick_ms;
	string gap_kind;
	ref array<float> pos;
	float expected_dt_s;
	float observed_dt_s;
	ref array<float> vel;
	ref array<float> vel_engine;
	bool vel_engine_available;
	float yaw_deg;
	bool yaw_deg_available;
	float pitch_deg;
	bool pitch_deg_available;
	int stance_idx;
	string command;
	bool falling;
	bool falling_available;
	bool jump;
	bool jump_available;
	bool parachute;
	bool parachute_available;
	bool in_vehicle;
	string vehicle_type;
	string vehicle_class;

	void MCPPlayersKinematicsRow()
	{
		pos = new array<float>();
		vel = new array<float>();
		vel_engine = new array<float>();
	}
};

//! Header and paged rows returned by players_kinematics.
class MCPPlayersKinematicsRead
{
	string schema;
	string mode;
	string trace_id;
	bool active;
	bool overflow;
	string stop_reason;
	int sample_hz;
	int interval_ms;
	int late_after_ms;
	int capacity;
	int count;
	int passes;
	int budget_skips;
	int pass_cost_last_ticks;
	int pass_cost_max_ticks;
	int pass_cost_budget_ticks;
	int start_tick_ms;
	int now_tick_ms;
	ref array<string> uids;
	int cursor;
	int next_cursor;
	bool eof;
	ref array<ref MCPPlayersKinematicsRow> rows;

	void MCPPlayersKinematicsRead()
	{
		uids = new array<string>();
		rows = new array<ref MCPPlayersKinematicsRow>();
	}
};

class MCPPlayersKinematics
{
	// Mirrored by tools/dayz_mcp/players_kinematics.py; tests keep both equal.
	static const string SCHEMA = "dayz-mcp-players-kinematics-v1";
	static const int SAMPLE_HZ_MIN = 5;
	static const int SAMPLE_HZ_MAX = 10;
	static const int MAX_SAMPLES_MIN = 1;
	static const int MAX_SAMPLES_MAX = 8192;
	static const int LIMIT_MAX = 256;
	static const int UIDS_MAX = 16;
	// TickCount advances in 100 ns units (10 000 ticks = 1 ms); 5000 = 0.5 ms.
	static const int PASS_BUDGET_TICKS = 5000;

	static bool s_Active;
	static bool s_Overflow;
	static string s_StopReason;
	static string s_LastError;
	static string s_TraceId;
	static int s_SampleHz;
	static int s_IntervalMs;
	static int s_LateAfterMs;
	static int s_Capacity;
	static int s_Count;
	static int s_Passes;
	static int s_BudgetSkips;
	static int s_PassCostLastTicks;
	static int s_PassCostMaxTicks;
	static int s_StartTickMs;
	static bool s_HasGrid;
	static int s_LastTickMs;
	static int s_NextDueMs;
	static bool s_SkipNextPass;
	static ref array<string> s_Uids;
	static ref array<Man> s_Players;
	static ref HumanMovementState s_MovementState;
	static ref map<string, ref MCPPlayersKinematicsPrev> s_PrevByUid;
	static ref array<ref MCPPlayersKinematicsRow> s_Rows;

	static bool Start(string traceId, int sampleHz, int maxSamples, array<string> uids)
	{
		int index;
		string uidEntry;
		MCPPlayersKinematicsRow row;
		s_LastError = "";
		if (s_TraceId != "")
		{
			s_LastError = "trace_exists";
			return false;
		}
		if (traceId == "" || sampleHz < SAMPLE_HZ_MIN || sampleHz > SAMPLE_HZ_MAX || maxSamples < MAX_SAMPLES_MIN || maxSamples > MAX_SAMPLES_MAX)
		{
			s_LastError = "bad_args";
			return false;
		}
		if (uids)
		{
			if (uids.Count() > UIDS_MAX)
			{
				s_LastError = "bad_args";
				return false;
			}
			index = 0;
			while (index < uids.Count())
			{
				uidEntry = uids.Get(index);
				if (uidEntry == "" || uidEntry.Trim() == "")
				{
					s_LastError = "bad_args";
					return false;
				}
				index = index + 1;
			}
		}

		// Preallocate rows and their three 3-component arrays so Capture only overwrites.
		s_Rows = new array<ref MCPPlayersKinematicsRow>();
		s_Rows.Resize(maxSamples);
		index = 0;
		while (index < maxSamples)
		{
			row = new MCPPlayersKinematicsRow();
			row.pos.Resize(3);
			row.vel.Resize(3);
			row.vel_engine.Resize(3);
			s_Rows.Set(index, row);
			index = index + 1;
		}

		s_Uids = new array<string>();
		if (uids)
		{
			index = 0;
			while (index < uids.Count())
			{
				s_Uids.Insert(uids.Get(index));
				index = index + 1;
			}
		}
		if (!s_Players)
		{
			s_Players = new array<Man>();
		}
		if (!s_MovementState)
		{
			s_MovementState = new HumanMovementState();
		}
		s_PrevByUid = new map<string, ref MCPPlayersKinematicsPrev>();

		s_TraceId = traceId;
		s_SampleHz = sampleHz;
		s_IntervalMs = 1000 / sampleHz;
		s_LateAfterMs = s_IntervalMs + (s_IntervalMs / 2);
		s_Capacity = maxSamples;
		s_Count = 0;
		s_Passes = 0;
		s_BudgetSkips = 0;
		s_PassCostLastTicks = 0;
		s_PassCostMaxTicks = 0;
		s_StartTickMs = 0;
		if (GetGame())
		{
			s_StartTickMs = GetGame().GetTime();
		}
		s_HasGrid = false;
		s_LastTickMs = 0;
		s_NextDueMs = 0;
		s_SkipNextPass = false;
		s_Overflow = false;
		s_StopReason = "";
		s_Active = true;
		return true;
	}

	// Called from MissionServer.OnUpdate on every frame. Idle returns before
	// calling GetGame(). Uses a non-catching-up schedule on GetTime() ms, and
	// skips the next due pass when a pass exceeds PASS_BUDGET_TICKS.
	static void Tick()
	{
		int nowMs;
		int startedTicks;
		int elapsedTicks;
		if (!s_Active)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		nowMs = GetGame().GetTime();
		if (s_HasGrid && nowMs < s_LastTickMs)
		{
			s_HasGrid = false;
		}
		s_LastTickMs = nowMs;
		if (s_HasGrid && nowMs < s_NextDueMs)
		{
			return;
		}
		if (!s_HasGrid || nowMs - s_NextDueMs >= s_IntervalMs)
		{
			s_NextDueMs = nowMs + s_IntervalMs;
		}
		else
		{
			s_NextDueMs = s_NextDueMs + s_IntervalMs;
		}
		s_HasGrid = true;
		if (s_SkipNextPass)
		{
			s_SkipNextPass = false;
			s_BudgetSkips = s_BudgetSkips + 1;
			return;
		}
		startedTicks = TickCount(0);
		Pass(nowMs);
		elapsedTicks = TickCount(startedTicks);
		if (elapsedTicks < 0)
		{
			return;
		}
		s_PassCostLastTicks = elapsedTicks;
		if (elapsedTicks > s_PassCostMaxTicks)
		{
			s_PassCostMaxTicks = elapsedTicks;
		}
		if (elapsedTicks > PASS_BUDGET_TICKS)
		{
			s_SkipNextPass = true;
		}
	}

	static bool Stop(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		if (!s_Active)
		{
			return true;
		}
		s_Active = false;
		s_StopReason = "requested";
		return true;
	}

	static bool Clear(string traceId)
	{
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}
		if (s_Active)
		{
			s_LastError = "trace_active";
			return false;
		}
		ClearState();
		return true;
	}

	// Lease close and mission shutdown: drop all trace state immediately.
	static void Abort(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		ClearState();
		s_LastError = reason;
	}

	static bool Matches(string traceId)
	{
		return s_TraceId != "" && s_TraceId == traceId;
	}

	static bool IsActive()
	{
		return s_Active;
	}

	static string GetLastError()
	{
		return s_LastError;
	}

	static MCPPlayersKinematicsRead View(string mode, string traceId, int cursor, int limit)
	{
		MCPPlayersKinematicsRead view;
		int index;
		int end;
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return null;
		}
		if (cursor < 0 || cursor > s_Count || limit < 1 || limit > LIMIT_MAX)
		{
			s_LastError = "bad_args";
			return null;
		}

		view = new MCPPlayersKinematicsRead();
		view.schema = SCHEMA;
		view.mode = mode;
		view.trace_id = s_TraceId;
		view.active = s_Active;
		view.overflow = s_Overflow;
		view.stop_reason = s_StopReason;
		view.sample_hz = s_SampleHz;
		view.interval_ms = s_IntervalMs;
		view.late_after_ms = s_LateAfterMs;
		view.capacity = s_Capacity;
		view.count = s_Count;
		view.passes = s_Passes;
		view.budget_skips = s_BudgetSkips;
		view.pass_cost_last_ticks = s_PassCostLastTicks;
		view.pass_cost_max_ticks = s_PassCostMaxTicks;
		view.pass_cost_budget_ticks = PASS_BUDGET_TICKS;
		view.start_tick_ms = s_StartTickMs;
		view.now_tick_ms = 0;
		if (GetGame())
		{
			view.now_tick_ms = GetGame().GetTime();
		}
		if (s_Uids)
		{
			index = 0;
			while (index < s_Uids.Count())
			{
				view.uids.Insert(s_Uids.Get(index));
				index = index + 1;
			}
		}
		view.cursor = cursor;

		end = cursor;
		if (mode == "read")
		{
			end = cursor + limit;
			if (end > s_Count)
			{
				end = s_Count;
			}
			index = cursor;
			while (index < end)
			{
				view.rows.Insert(s_Rows.Get(index));
				index = index + 1;
			}
		}
		view.next_cursor = end;
		view.eof = !s_Active && end == s_Count;
		return view;
	}

	// Closed gap classification in strict precedence order. Takes no position:
	// an in-cadence position jump is not a gap and is published as measured.
	static string GapKind(bool hasPrevious, int previousTickMs, int nowTickMs, int previousPass, int nowPass, int lateAfterMs)
	{
		if (!hasPrevious)
		{
			return "first_sample";
		}
		if (nowTickMs <= previousTickMs)
		{
			return "clock_not_monotonic";
		}
		if (nowPass - previousPass > 1)
		{
			return "subject_absent";
		}
		if (nowTickMs - previousTickMs > lateAfterMs)
		{
			return "pass_late";
		}
		return "";
	}

	// Native 1.30 Transport families: Helicopter (aircraft), Car/Motorbike
	// (ground), Boat (water), any other Transport (unknown), or null (none).
	static string VehicleClass(Transport transport)
	{
		if (!transport)
		{
			return "none";
		}
		if (Helicopter.Cast(transport))
		{
			return "aircraft";
		}
		if (Car.Cast(transport) || Motorbike.Cast(transport))
		{
			return "ground";
		}
		if (Boat.Cast(transport))
		{
			return "water";
		}
		return "unknown";
	}

	protected static void Pass(int nowMs)
	{
		int index;
		PlayerBase player;
		PlayerIdentity identity;
		string uid;
		if (!s_Active || !GetGame())
		{
			return;
		}
		s_Passes = s_Passes + 1;
		s_Players.Clear();
		GetGame().GetPlayers(s_Players);
		index = 0;
		while (index < s_Players.Count())
		{
			player = PlayerBase.Cast(s_Players.Get(index));
			if (player && player.IsAlive())
			{
				identity = player.GetIdentity();
				if (identity)
				{
					uid = identity.GetPlainId();
					if (uid != "" && (!s_Uids || s_Uids.Count() == 0 || s_Uids.Find(uid) >= 0))
					{
						if (s_Count >= s_Capacity)
						{
							s_Overflow = true;
							s_Active = false;
							s_StopReason = "overflow";
							return;
						}
						Capture(player, uid, nowMs);
					}
				}
			}
			index = index + 1;
		}
	}

	protected static void Capture(PlayerBase player, string uid, int nowMs)
	{
		MCPPlayersKinematicsRow row;
		MCPPlayersKinematicsPrev prev;
		bool hasPrev;
		int prevTickMs;
		int prevPass;
		vector prevPos;
		vector position;
		vector velocity;
		float dtS;
		HumanCommandVehicle vehicleCmd;
		Transport transport;
		string vehType;
		HumanInputController hic;
		HumanCommandWeapons weaponCmd;

		row = s_Rows.Get(s_Count);
		row.uid = uid;
		row.tick_ms = nowMs;

		position = player.GetPosition();
		row.pos.Set(0, position[0]);
		row.pos.Set(1, position[1]);
		row.pos.Set(2, position[2]);

		row.expected_dt_s = s_IntervalMs * 0.001;

		hasPrev = false;
		prevTickMs = 0;
		prevPass = 0;
		prevPos = vector.Zero;
		prev = null;
		if (s_PrevByUid)
		{
			prev = s_PrevByUid.Get(uid);
		}
		if (prev)
		{
			hasPrev = true;
			prevTickMs = prev.tick_ms;
			prevPass = prev.pass_index;
			prevPos = prev.pos;
		}

		row.gap_kind = GapKind(hasPrev, prevTickMs, nowMs, prevPass, s_Passes, s_LateAfterMs);

		row.observed_dt_s = 0.0;
		row.vel.Set(0, 0.0);
		row.vel.Set(1, 0.0);
		row.vel.Set(2, 0.0);
		if (hasPrev)
		{
			dtS = (nowMs - prevTickMs) * 0.001;
			row.observed_dt_s = dtS;
			if (dtS > 0.0)
			{
				row.vel.Set(0, (position[0] - prevPos[0]) / dtS);
				row.vel.Set(1, (position[1] - prevPos[1]) / dtS);
				row.vel.Set(2, (position[2] - prevPos[2]) / dtS);
			}
		}

		transport = null;
		vehicleCmd = player.GetCommand_Vehicle();
		if (vehicleCmd)
		{
			transport = vehicleCmd.GetTransport();
		}
		if (!transport)
		{
			transport = Transport.Cast(player.GetParent());
		}

		row.vel_engine.Set(0, 0.0);
		row.vel_engine.Set(1, 0.0);
		row.vel_engine.Set(2, 0.0);
		row.vel_engine_available = false;
		row.falling = false;
		row.falling_available = false;

		if (transport)
		{
			row.in_vehicle = true;
			vehType = transport.GetType();
			if (vehType == "")
			{
				vehType = transport.ClassName();
			}
			row.vehicle_type = vehType;
			row.vehicle_class = VehicleClass(transport);
		}
		else
		{
			row.in_vehicle = false;
			row.vehicle_type = "-";
			row.vehicle_class = VehicleClass(null);
			velocity = vector.Zero;
			player.PhysicsGetVelocity(velocity);
			row.vel_engine.Set(0, velocity[0]);
			row.vel_engine.Set(1, velocity[1]);
			row.vel_engine.Set(2, velocity[2]);
			row.vel_engine_available = true;
			// pValidate false: pure read of the physics controller fall state.
			row.falling = player.PhysicsIsFalling(false);
			row.falling_available = true;
		}

		row.yaw_deg = 0.0;
		row.yaw_deg_available = false;
		hic = player.GetInputController();
		if (hic)
		{
			row.yaw_deg = MCPPlayerTrace.CompassDeg(hic.GetHeadingAngle());
			row.yaw_deg_available = true;
		}

		row.pitch_deg = 0.0;
		row.pitch_deg_available = false;
		weaponCmd = player.GetCommandModifier_Weapons();
		if (weaponCmd)
		{
			row.pitch_deg = weaponCmd.GetBaseAimingAngleUD();
			row.pitch_deg_available = true;
		}

		player.GetMovementState(s_MovementState);
		row.stance_idx = s_MovementState.m_iStanceIdx;
		row.command = MCPPlayerTrace.CommandName(s_MovementState.m_CommandTypeId);

		row.jump = player.IsJumpInProgress();
		row.jump_available = true;

		// Vanilla 1.30 has no parachute system.
		row.parachute = false;
		row.parachute_available = false;

		if (!prev)
		{
			prev = new MCPPlayersKinematicsPrev();
			s_PrevByUid.Insert(uid, prev);
		}
		prev.tick_ms = nowMs;
		prev.pos = position;
		prev.pass_index = s_Passes;

		s_Count = s_Count + 1;
	}

	protected static void ClearState()
	{
		s_Active = false;
		s_Overflow = false;
		s_StopReason = "";
		s_TraceId = "";
		s_SampleHz = 0;
		s_IntervalMs = 0;
		s_LateAfterMs = 0;
		s_Capacity = 0;
		s_Count = 0;
		s_Passes = 0;
		s_BudgetSkips = 0;
		s_PassCostLastTicks = 0;
		s_PassCostMaxTicks = 0;
		s_StartTickMs = 0;
		s_HasGrid = false;
		s_LastTickMs = 0;
		s_NextDueMs = 0;
		s_SkipNextPass = false;
		if (s_Uids)
		{
			s_Uids.Clear();
			s_Uids = null;
		}
		if (s_Players)
		{
			s_Players.Clear();
		}
		if (s_PrevByUid)
		{
			s_PrevByUid.Clear();
			s_PrevByUid = null;
		}
		if (s_Rows)
		{
			s_Rows.Clear();
			s_Rows = null;
		}
	}
};
