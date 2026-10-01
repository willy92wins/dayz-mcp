// player_trace (inbox 4ae5): a per-frame trace of the local player on this
// owner client, the client that drives the player's movement. 4_World, like
// MCPVehicleTraceRead and MCPAnimTimelineRead: View builds the read and 4_World
// cannot see 5_Mission. config.cpp compiles the whole folder.
//
// MissionGameplay.OnUpdate calls MCPClientBridge.OnTick, and OnTick calls Tick on
// every frame, before the job runner and before any early return of the bridge.
// The sampler is never driven from a job, a vehicle or CommandHandler, so it runs
// seated or not and needs no window focus; its rate is bounded by the client's
// frame rate. The lifecycle is vehicle_trace's (MCP_CarScript.c MCPVehicleTrace):
// start, status, stop with an autodump, dump, read by cursor and limit, clear.
// The decimation is not: see Tick, which drops the time slow frames owe instead
// of catching it up. A death or change of the local player stops the trace with
// that stop_reason, and a dead player cannot start one (player_dead). Health is
// not sampled: vanilla reads the local player's GetHealth only when not
// multiplayer (ingamehud.c:698-709), so the owner client has no synced value.

//! An entity under, carrying or holding the player. type is Object.GetType()
//! (object.c:473), or ClassName() when the entity is no Object or GetType() is
//! empty; class_name is ClassName() (enscript.c:37). net_id_low and net_id_high
//! are Object.GetNetworkID (object.c:813-815), the two ints MCP results carry,
//! 0 0 for an entity that is no Object. pos is IEntity.GetOrigin (enentity.c:368).
class MCPPlayerTraceEntity
{
	string type;
	string class_name;
	int net_id_low;
	int net_id_high;
	ref array<float> pos;

	void MCPPlayerTraceEntity()
	{
		pos = new array<float>();
	}
};

//! One sample. monotonic_s is client GetTickTime seconds and tick the client
//! bridge OnTick count (the counter a result's tick_dispatch reports). pos is
//! PhysicsGetPositionWS (human.c:1348) and vel PhysicsGetVelocity (human.c:1410).
//! heading_deg is the compass heading of HumanInputController.GetHeadingAngle,
//! the input heading (0 = north = +Z, 90 = east = +X; -1 without an input
//! controller); yaw_deg is the body's Object.GetOrientation yaw in degrees
//! (object.c:307-311), raw, as vehicle_trace's yaw_deg. falling is
//! PhysicsIsFalling(false) (human.c:1397). floor is PhysicsGetFloorEntity
//! (human.c:1400), linked PhysicsGetLinkedEntity (human.c:1402-1403, not valid
//! while parent is set) and parent IEntity.GetParent (enentity.c:571), each unset
//! when there is none. sliding_off_linked is PhysicsWasSlidingOffLinkedEntity
//! (human.c:1405-1407). command_type_id, stance_idx and movement_idx are
//! HumanMovementState's m_CommandTypeId, m_iStanceIdx and m_iMovement
//! (human.c:1153-1157); command names the command id.
class MCPPlayerTraceSample
{
	int sequence;
	float monotonic_s;
	float sample_dt_s;
	int tick;
	ref array<float> pos;
	ref array<float> vel;
	float heading_deg;
	float yaw_deg;
	bool falling;
	ref MCPPlayerTraceEntity floor;
	ref MCPPlayerTraceEntity linked;
	bool sliding_off_linked;
	ref MCPPlayerTraceEntity parent;
	int command_type_id;
	string command;
	int stance_idx;
	int movement_idx;

	void MCPPlayerTraceSample()
	{
		pos = new array<float>();
		vel = new array<float>();
	}
};

class MCPPlayerTraceRead
{
	string schema;
	string mode;
	string trace_id;
	bool active;
	bool complete;
	bool overflow;
	string stop_reason;
	int sample_hz;
	int capacity;
	int count;
	float start_monotonic_s;
	string player_type;
	int net_id_low;
	int net_id_high;
	int cursor;
	int next_cursor;
	bool eof;
	string path;
	int rows;
	ref array<ref MCPPlayerTraceSample> samples;

	void MCPPlayerTraceRead()
	{
		samples = new array<ref MCPPlayerTraceSample>();
	}
};

class MCPPlayerTrace
{
	static const string SCHEMA = "dayz-mcp-player-trace-v1";
	static const string DUMP_PREFIX = "$profile:dayz_mcp_player_trace_";
	// Mirrored by player_trace.py; tools/tests keep the copies equal.
	static const int SAMPLE_HZ_MIN = 20;
	static const int SAMPLE_HZ_MAX = 60;
	static const int MAX_SAMPLES_MIN = 2;
	static const int MAX_SAMPLES_MAX = 8192;
	static const int LIMIT_MAX = 64;

	static bool s_Active;
	static bool s_Complete;
	static bool s_Overflow;
	static string s_StopReason;
	static string s_LastError;
	static string s_TraceId;
	static int s_SampleHz;
	static int s_Capacity;
	static int s_Count;
	static float s_StartMonotonicS;
	static float s_LastSampleS;
	// GetTickTime at which the next sample is due (see Tick).
	static float s_NextDueS;
	static PlayerBase s_Player;
	static string s_PlayerType;
	static int s_NetIdLow;
	static int s_NetIdHigh;
	static string s_DumpPath;
	static int s_DumpRows;
	static ref HumanMovementState s_MovementState;
	static ref array<ref MCPPlayerTraceSample> s_Samples;

	static bool Start(PlayerBase player, string traceId, int sampleHz, int maxSamples)
	{
		int index;
		MCPPlayerTraceSample sample;
		s_LastError = "";
		if (!player)
		{
			s_LastError = "no_player";
			return false;
		}
		// Object.IsAlive (object.c:523-526). A dead player's trace would stop
		// on its first frame, so it is refused here instead.
		if (!player.IsAlive())
		{
			s_LastError = "player_dead";
			return false;
		}
		if (s_TraceId != "")
		{
			s_LastError = "trace_exists";
			return false;
		}
		if (sampleHz < SAMPLE_HZ_MIN || sampleHz > SAMPLE_HZ_MAX || maxSamples < MAX_SAMPLES_MIN || maxSamples > MAX_SAMPLES_MAX)
		{
			s_LastError = "bad_args";
			return false;
		}

		// Preallocated with their pos and vel: a capture only overwrites them.
		s_Samples = new array<ref MCPPlayerTraceSample>();
		s_Samples.Resize(maxSamples);
		index = 0;
		while (index < maxSamples)
		{
			sample = new MCPPlayerTraceSample();
			sample.pos.Resize(3);
			sample.vel.Resize(3);
			s_Samples.Set(index, sample);
			index = index + 1;
		}
		if (!s_MovementState)
		{
			s_MovementState = new HumanMovementState();
		}

		s_TraceId = traceId;
		s_SampleHz = sampleHz;
		s_Capacity = maxSamples;
		s_Count = 0;
		s_StartMonotonicS = GetGame().GetTickTime();
		s_LastSampleS = 0.0;
		s_NextDueS = 0.0;
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_Player = player;
		s_PlayerType = player.GetType();
		s_NetIdLow = 0;
		s_NetIdHigh = 0;
		player.GetNetworkID(s_NetIdLow, s_NetIdHigh);
		s_Active = true;
		return true;
	}

	// Called from MCPClientBridge.OnTick on every client frame. Idle returns
	// before any engine call. At most one sample per frame, due on a grid of
	// 1/sample_hz of GetTickTime: the next sample is due one interval after the
	// previous due time, so a frame that comes a little late does not shift the
	// grid. A frame a full interval or more past its due time restarts the grid
	// one interval after itself, so the time slow frames owe is dropped: after
	// the frame rate recovers, the trace does not catch up above sample_hz. That
	// departs on purpose from MCPVehicleTrace.Capture, whose accumulator keeps
	// the debt and samples every frame until it is repaid. A frame before the due
	// time, an equal tick time included, waits; a backward one fails the trace.
	static void Tick(int bridgeTick)
	{
		float intervalS;
		float nowS;
		if (!s_Active)
		{
			return;
		}
		if (!GetGame())
		{
			return;
		}
		if (!CheckPlayer())
		{
			return;
		}

		nowS = GetGame().GetTickTime();
		if (s_Count > 0 && nowS < s_LastSampleS)
		{
			Fail("clock_not_monotonic");
			return;
		}
		if (s_Count > 0 && nowS < s_NextDueS)
		{
			return;
		}
		intervalS = 1.0 / s_SampleHz;
		if (s_Count == 0 || nowS - s_NextDueS >= intervalS)
		{
			s_NextDueS = nowS + intervalS;
		}
		else
		{
			s_NextDueS = s_NextDueS + intervalS;
		}
		CaptureNow(nowS, bridgeTick);
	}

	// false, with the trace stopped and its reason recorded, when the local
	// player is gone, another one, or dead. Also run by the dispatcher before
	// it answers on an active trace.
	static bool CheckPlayer()
	{
		PlayerBase live;
		if (!s_Active)
		{
			return false;
		}
		live = null;
		if (GetGame())
		{
			live = PlayerBase.Cast(GetGame().GetPlayer());
		}
		if (!live)
		{
			Fail("player_changed");
			return false;
		}
		if (live != s_Player)
		{
			Fail("player_changed");
			return false;
		}
		if (!live.IsAlive())
		{
			Fail("player_dead");
			return false;
		}
		return true;
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
		s_Complete = true;
		s_StopReason = "requested";
		if (!Dump(traceId))
		{
			return false;
		}
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

	// Shutdown: autodump what was sampled before the wipe, as
	// MCPVehicleTrace.Abort does (fb-20260915-014739-7ad1).
	static void Abort(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		if (s_Count > 0)
		{
			s_Active = false;
			if (s_StopReason == "")
			{
				s_StopReason = reason;
			}
			Dump(s_TraceId);
		}
		ClearState();
		s_LastError = reason;
	}

	static void Fail(string reason)
	{
		if (s_TraceId == "")
		{
			return;
		}
		s_Active = false;
		s_Complete = false;
		s_StopReason = reason;
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

	static bool Dump(string traceId)
	{
		string filePath;
		FileHandle handle;
		MCPPlayerTraceRead header;
		JsonSerializer serializer;
		string line;
		bool wrote;
		int index;
		s_LastError = "";
		if (!Matches(traceId))
		{
			s_LastError = "trace_not_found";
			return false;
		}

		filePath = DUMP_PREFIX + traceId + ".jsonl";
		handle = OpenFile(filePath, FileMode.WRITE);
		if (handle == 0)
		{
			s_LastError = "dump_failed";
			return false;
		}

		header = View("dump", traceId, 0, 1);
		if (!header)
		{
			CloseFile(handle);
			s_LastError = "dump_failed";
			return false;
		}
		header.path = filePath;
		header.rows = s_Count;
		header.samples.Clear();

		serializer = new JsonSerializer();
		line = "";
		wrote = serializer.WriteToString(header, false, line);
		if (!wrote)
		{
			CloseFile(handle);
			s_LastError = "dump_failed";
			return false;
		}
		FPrintln(handle, line);

		index = 0;
		while (index < s_Count)
		{
			line = "";
			wrote = serializer.WriteToString(s_Samples.Get(index), false, line);
			if (!wrote)
			{
				CloseFile(handle);
				s_LastError = "dump_failed";
				return false;
			}
			FPrintln(handle, line);
			index = index + 1;
		}

		CloseFile(handle);
		s_DumpPath = filePath;
		s_DumpRows = s_Count;
		return true;
	}

	static MCPPlayerTraceRead View(string mode, string traceId, int cursor, int limit)
	{
		MCPPlayerTraceRead view;
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

		view = new MCPPlayerTraceRead();
		view.schema = SCHEMA;
		view.mode = mode;
		view.trace_id = s_TraceId;
		view.active = s_Active;
		view.complete = s_Complete;
		view.overflow = s_Overflow;
		view.stop_reason = s_StopReason;
		view.sample_hz = s_SampleHz;
		view.capacity = s_Capacity;
		view.count = s_Count;
		view.start_monotonic_s = s_StartMonotonicS;
		view.player_type = s_PlayerType;
		view.net_id_low = s_NetIdLow;
		view.net_id_high = s_NetIdHigh;
		view.cursor = cursor;
		view.path = s_DumpPath;
		view.rows = s_DumpRows;

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
				view.samples.Insert(s_Samples.Get(index));
				index = index + 1;
			}
		}
		view.next_cursor = end;
		view.eof = !s_Active && end == s_Count;
		return view;
	}

	protected static void CaptureNow(float nowS, int bridgeTick)
	{
		MCPPlayerTraceSample sample;
		HumanInputController hic;
		vector position;
		vector velocity;
		vector orientation;
		if (!s_Active || !s_Player)
		{
			return;
		}
		// Full: stop and flag overflow instead of overwriting, like the
		// vehicle trace. The dropped sample is the one that did not fit.
		if (s_Count >= s_Capacity)
		{
			s_Overflow = true;
			Fail("overflow");
			return;
		}
		if (s_Count > 0 && nowS <= s_LastSampleS)
		{
			Fail("clock_not_monotonic");
			return;
		}

		sample = s_Samples.Get(s_Count);
		sample.sequence = s_Count;
		sample.monotonic_s = nowS;
		if (s_Count == 0)
		{
			sample.sample_dt_s = 0.0;
		}
		else
		{
			sample.sample_dt_s = nowS - s_LastSampleS;
		}
		sample.tick = bridgeTick;

		position = s_Player.PhysicsGetPositionWS();
		sample.pos.Set(0, position[0]);
		sample.pos.Set(1, position[1]);
		sample.pos.Set(2, position[2]);
		velocity = vector.Zero;
		s_Player.PhysicsGetVelocity(velocity);
		sample.vel.Set(0, velocity[0]);
		sample.vel.Set(1, velocity[1]);
		sample.vel.Set(2, velocity[2]);

		sample.heading_deg = -1.0;
		hic = s_Player.GetInputController();
		if (hic)
		{
			sample.heading_deg = CompassDeg(hic.GetHeadingAngle());
		}
		orientation = s_Player.GetOrientation();
		sample.yaw_deg = orientation[0];

		// pValidate false: the read vanilla's CommandHandler makes on every tick
		// to start a fall from any other command (dayzplayerimplement.c:2540).
		// true is its check after a finished command (:2384); what it validates
		// is undocumented.
		sample.falling = s_Player.PhysicsIsFalling(false);
		sample.floor = DescribeEntity(s_Player.PhysicsGetFloorEntity());
		sample.linked = DescribeEntity(s_Player.PhysicsGetLinkedEntity());
		sample.sliding_off_linked = s_Player.PhysicsWasSlidingOffLinkedEntity();
		sample.parent = DescribeEntity(s_Player.GetParent());

		s_Player.GetMovementState(s_MovementState);
		sample.command_type_id = s_MovementState.m_CommandTypeId;
		sample.command = CommandName(s_MovementState.m_CommandTypeId);
		sample.stance_idx = s_MovementState.m_iStanceIdx;
		sample.movement_idx = s_MovementState.m_iMovement;

		s_LastSampleS = nowS;
		s_Count = s_Count + 1;
	}

	// Compass degrees, 0..360 with 360 excluded (0 = +Z north, 90 = +X east), of
	// a GetHeadingAngle value: radians, -PI..PI (human.c:27-28). Vanilla turns a
	// heading h into the facing x = cos(h + PI/2), z = sin(h + PI/2)
	// (miscgameplayfunctions.c:726-733), so h = 0 faces +Z, h grows
	// counter-clockwise and the compass yaw is -h (crosshairselector.c:254).
	// 0.0 minus the product keeps a heading of 0 at +0, never -0.
	protected static float CompassDeg(float headingRad)
	{
		float compass;
		compass = 0.0 - headingRad * Math.RAD2DEG;
		if (compass < 0.0)
		{
			compass = compass + 360.0;
		}
		if (compass >= 360.0)
		{
			compass = compass - 360.0;
		}
		return compass;
	}

	// null without an entity. A present one costs one small object per sample;
	// the samples and their arrays are preallocated at Start.
	protected static MCPPlayerTraceEntity DescribeEntity(IEntity entity)
	{
		MCPPlayerTraceEntity described;
		Object asObject;
		string configType;
		vector origin;
		int lowBits;
		int highBits;
		if (!entity)
		{
			return null;
		}
		described = new MCPPlayerTraceEntity();
		described.class_name = entity.ClassName();
		described.type = described.class_name;
		asObject = Object.Cast(entity);
		if (asObject)
		{
			configType = asObject.GetType();
			if (configType != "")
			{
				described.type = configType;
			}
			lowBits = 0;
			highBits = 0;
			asObject.GetNetworkID(lowBits, highBits);
			described.net_id_low = lowBits;
			described.net_id_high = highBits;
		}
		origin = entity.GetOrigin();
		described.pos.Insert(origin[0]);
		described.pos.Insert(origin[1]);
		described.pos.Insert(origin[2]);
		return described;
	}

	// DayZPlayerConstants.COMMANDID_* are set by the engine (dayzplayer.c:695-708),
	// so the id is named against them at run time.
	protected static string CommandName(int commandId)
	{
		if (commandId == DayZPlayerConstants.COMMANDID_MOVE)
		{
			return "move";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_ACTION)
		{
			return "action";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_MELEE)
		{
			return "melee";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_MELEE2)
		{
			return "melee2";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_FALL)
		{
			return "fall";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_DEATH)
		{
			return "death";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_DAMAGE)
		{
			return "damage";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_LADDER)
		{
			return "ladder";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_UNCONSCIOUS)
		{
			return "unconscious";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_SWIM)
		{
			return "swim";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_VEHICLE)
		{
			return "vehicle";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_CLIMB)
		{
			return "climb";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_SCRIPT)
		{
			return "script";
		}
		if (commandId == DayZPlayerConstants.COMMANDID_NONE)
		{
			return "none";
		}
		return "other";
	}

	protected static void ClearState()
	{
		s_Active = false;
		s_Complete = false;
		s_Overflow = false;
		s_StopReason = "";
		s_TraceId = "";
		s_SampleHz = 0;
		s_Capacity = 0;
		s_Count = 0;
		s_StartMonotonicS = 0.0;
		s_LastSampleS = 0.0;
		s_NextDueS = 0.0;
		s_Player = null;
		s_PlayerType = "";
		s_NetIdLow = 0;
		s_NetIdHigh = 0;
		s_DumpPath = "";
		s_DumpRows = 0;
		if (s_Samples)
		{
			s_Samples.Clear();
			s_Samples = null;
		}
	}
};
