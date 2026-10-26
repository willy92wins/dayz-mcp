class MCPBridge : Managed
{
	protected const int MAX_DISPATCH_PER_TICK = 4;
	protected const int MAX_PENDING = 32;
	protected const int PENDING_POLL_THRESHOLD = 8;
	protected const int MAX_CALLBACK_REFS = 128;
	// Reserve the existing daemon ingress cap (loopback.py MAX_QUEUE) per poll.
	protected const int MAX_POLL_RESULTS = 64;
	protected const float JOB_TIMEOUT_S = 5.0;
	protected const int VEHICLE_SEARCH_RADIUS = 4;
	// 8 m covers a truck-length hull around the subject's origin after ECE_TRACE snap.
	protected const float SPAWN_READY_RADIUS = 8.0;
	// world_spawn lifetime_s upper bound, in seconds: 3888000 (45 days) is the largest
	// <lifetime> in dayzOffline.chernarusplus db/types.xml (Barrel_Blue among others).
	// Mirrors WORLD_SPAWN_LIFETIME_MAX_S in tools/dayz_mcp/server.py.
	protected const float SPAWN_LIFETIME_MAX_S = 3888000.0;
	protected const int TELEMETRY_ITEMS_CAP = 16;
	protected const int TELEMETRY_JSONL_DEFAULT_MAX_LINES = 64;
	protected const int MAX_JSONL_LINE_CHARS = 4096;
	protected const float RAYCAST_MAX_RADIUS = 5.0;
	protected const float TELEMETRY_OBJECT_AT_MAX_RADIUS = 50.0;
	// F3.4 / F3.6: fixed lookup radius when type+pos resolve an in-world object.
	protected const float OBJECT_LOOKUP_RADIUS = 25.0;
	// object_resolve only. Callers pass this radius; it is not the 25 m lookup.
	protected const float OBJECT_RESOLVE_RADIUS_MAX = 50.0;
	// object_doors refuses a larger GetDoorCount instead of emitting an unbounded list.
	protected const int DOOR_READ_MAX = 64;
	// vehicle_door calls ForceUpdateLightsEnd this long after its write. Vanilla
	// ends the pulse in OnEndServer (actioncardoorsoutside.c:109-118); 1000 ms spans
	// the door's animPeriod of 0.5 s (config.cpp CarScript AnimationSources).
	protected const int VEHICLE_DOOR_LIGHTS_PULSE_MS = 1000;
	// loadout_get / loadout_apply (fb99, 220f): a loadout holds at most this many
	// items, at most this many levels below the target (what the target wears or
	// holds is level 1). Mirrors MAX_ITEMS and MAX_DEPTH in tools/dayz_mcp/loadout.py.
	protected const int LOADOUT_MAX_ITEMS = 64;
	protected const int LOADOUT_MAX_DEPTH = 4;
	// server_perf window_s bounds in seconds, and nav_path_query's snap_radius
	// and players_visibility's radius caps in metres. Mirrored by server.py and
	// loopback.py; a test pins the values in all three and the comparisons here.
	protected const float SERVER_PERF_WINDOW_MIN_S = 1.0;
	protected const float SERVER_PERF_WINDOW_MAX_S = 60.0;
	protected const float NAV_SNAP_RADIUS_MAX = 50.0;
	protected const float VISIBILITY_RADIUS_MAX = 100.0;
	// players_visibility: the field of view either side of the heading, in
	// degrees; the head bone; the head height over the position used when that
	// bone is missing; and the rvproxy radius scene_raycast runs by default
	// (ValidateRaycastArgs).
	protected const float VISIBILITY_HALF_FOV_DEG = 60.0;
	protected const string VISIBILITY_HEAD_BONE = "Head";
	protected const float VISIBILITY_HEAD_FALLBACK_M = 1.6;
	protected const float VISIBILITY_RAY_RADIUS = 0.05;
	// Capability census announced on every poll (caps=). Sorted ascending,
	// comma separated; one entry per branch of Dispatch() before unknown_command.
	// The daemon crosses this list against its registered tools; keep it in
	// lockstep with the dispatcher and never derive it from the daemon side.
	// Short literals joined by + (the vanilla form for a const string built from
	// pieces); the longest single literal in the vanilla scripts is about 240 chars.
	protected const string SERVER_CAPABILITIES = "action_cursor_ids,bot_start,bot_stop,entities_query,exec_enforce,hands_attach,hands_take,infected_drive,inventory_attach,inventory_give,inventory_move," + "loadout_apply,loadout_clear,loadout_get,nav_path_query,notify_players,object_anim,object_delete,object_doors,object_inspect,object_resolve,player_godmode,player_heal,player_kill,player_teleport," + "players_visibility,query_all_players,query_get_in_condition,query_player_state,scene_raycast,server_perf,surface_query," + "telemetry_read,vehicle_door,vehicle_enter,vehicle_prepare_fixture,weapon_state,world_spawn," + "world_time_get,world_time_set,world_weather_set";
	// Arg-contract hash (fb-20260924-235528-0878). 16-hex sha256 prefix of the
	// canonical server arg contract; must equal EXPECTED_SERVER_ARG_CONTRACT_HASH
	// in tools/dayz_mcp/server.py. Announced as poll ach= so a stale PBO that
	// still lists the same command names fails the version/capability gate.
	protected const string SERVER_ARG_CONTRACT_HASH = "4e6500849680c89b";

	protected static ref MCPBridge m_Instance;

	protected RestContext m_Ctx;
	protected string m_Url;
	protected string m_Key;
	protected string m_PeerInstance;
	protected string m_PollVersion;
	protected float m_PollHz;
	protected float m_Accum;
	protected float m_ElapsedS;
	// Backoff at which a poll failure stops looking transient and the credential on
	// disk is re-read.
	protected const float KEY_RELOAD_BACKOFF_S = 4.0;
	protected float m_Backoff;
	protected int m_Tick;
	protected int m_TickPollSent;
	protected int m_TickPollCallback;
	protected bool m_PollInFlight;
	protected bool m_Configured;
	protected bool m_InitFailureLogged;
	protected bool m_ResultDropLogged;
	protected ref array<ref RestCallback> m_CallbackRefs;
	protected ref MCPPollCallback m_PollCallback;
	protected ref array<ref MCPResultCallback> m_ResultCallbackPool;
	protected ref array<Man> m_Players;
	protected ref array<ref MCPCommand> m_Pending;
	protected ref map<int, ref MCPJob> m_Jobs;
	protected ref map<int, Object> m_RuntimeObjects;
	protected ref array<Object> m_ReadyObjects;
	protected ref array<CargoBase> m_ReadyProxyCargos;
	protected ref array<ref MCPTelemetryFixtureLine> m_TelemetryFixtureLinePool;

	void MCPBridge()
	{
		int fixtureLineIndex = 0;
		m_PollHz = 5.0;
		m_Accum = 0.0;
		m_ElapsedS = 0.0;
		m_Backoff = 0.0;
		m_Tick = 0;
		m_TickPollSent = 0;
		m_TickPollCallback = 0;
		m_PollVersion = "";
		m_PeerInstance = "";
		m_PollInFlight = false;
		m_Configured = false;
		m_InitFailureLogged = false;
		m_ResultDropLogged = false;
		m_CallbackRefs = new array<ref RestCallback>();
		m_ResultCallbackPool = new array<ref MCPResultCallback>();
		m_Players = new array<Man>();
		m_Pending = new array<ref MCPCommand>();
		m_Jobs = new map<int, ref MCPJob>();
		m_RuntimeObjects = new map<int, Object>();
		m_ReadyObjects = new array<Object>();
		m_ReadyProxyCargos = new array<CargoBase>();
		m_TelemetryFixtureLinePool = new array<ref MCPTelemetryFixtureLine>();
		while (fixtureLineIndex < TELEMETRY_JSONL_DEFAULT_MAX_LINES)
		{
			m_TelemetryFixtureLinePool.Insert(new MCPTelemetryFixtureLine());
			fixtureLineIndex = fixtureLineIndex + 1;
		}
		if (g_Game)
		{
			g_Game.Event_OnRPC.Insert(MCPOnInventoryMoveRefused);
		}
	}

	void ~MCPBridge()
	{
		if (g_Game)
		{
			g_Game.Event_OnRPC.Remove(MCPOnInventoryMoveRefused);
		}
	}

	static MCPBridge Get()
	{
		if (!m_Instance)
		{
			m_Instance = new MCPBridge();
		}

		return m_Instance;
	}

	void OnTick(float timeslice)
	{
		m_Tick = m_Tick + 1;
		m_ElapsedS = m_ElapsedS + timeslice;
		MCPBotControl.Tick(this, m_ElapsedS);

		if (!m_Configured)
		{
			TryInit();
			return;
		}

		ProcessJobs();
		DrainPending();

		if (m_PollInFlight)
		{
			return;
		}

		if (m_Pending && m_Pending.Count() > PENDING_POLL_THRESHOLD)
		{
			return;
		}

		m_Accum = m_Accum + timeslice;

		float interval = 1.0 / m_PollHz;
		float wait = interval + m_Backoff;
		if (m_Accum < wait)
		{
			return;
		}

		StartPoll();
	}

	protected void TryInit()
	{
		RestApi api = GetRestApi();
		if (!api)
		{
			api = CreateRestApi();
		}

		if (!api)
		{
			LogInitFailure("RestApi unavailable");
			return;
		}

		MCPConfig cfg = new MCPConfig();
		string path = "$profile:dayz_mcp.json";

		if (!FileExist(path))
		{
			path = "$mission:dayz_mcp.json";
		}

		if (!FileExist(path))
		{
			LogInitFailure("config not found");
			return;
		}

		JsonFileLoader<MCPConfig>.JsonLoadFile(path, cfg);

		if (!cfg.url || cfg.url == "")
		{
			LogInitFailure("config url missing");
			return;
		}

		if (!cfg.key || cfg.key == "")
		{
			LogInitFailure("config key missing");
			return;
		}

		if (!IsLoopbackBridgeUrl(cfg.url))
		{
			LogInitFailure("config url not loopback");
			return;
		}

		m_Url = cfg.url;
		m_Key = cfg.key;
		m_PeerInstance = "";
		if (cfg.instance != "")
		{
			m_PeerInstance = cfg.instance;
		}
		if (cfg.pollHz > 0.0)
		{
			m_PollHz = cfg.pollHz;
			if (m_PollHz > 60.0)
			{
				m_PollHz = 60.0;
			}
		}

		m_Ctx = api.GetRestContext(m_Url);
		if (!m_Ctx)
		{
			LogInitFailure("RestContext unavailable");
			return;
		}

		m_Ctx.SetHeader("application/json");
		m_Configured = true;
		m_Backoff = 0.0;
		m_Accum = 0.0;
		Log("config loaded path=" + path + " url=" + m_Url + " keylen=" + m_Key.Length() + " instlen=" + m_PeerInstance.Length() + " poll_hz=" + m_PollHz);
	}

	protected void LogInitFailure(string reason)
	{
		if (m_InitFailureLogged)
		{
			return;
		}

		m_InitFailureLogged = true;
		Log("init pending: " + reason);
	}

	protected void StartPoll()
	{
		// Count accepted work too: jobs/pending each still owe one result.
		// Pause admission only; draining and terminal POSTs must keep running.
		if (m_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS)
		{
			return;
		}

		m_Accum = 0.0;
		m_PollInFlight = true;
		m_TickPollSent = m_Tick;

		// Reuse across completed polls, as the client does. A native-retained
		// RestCallback must not turn every successful poll into another object.
		if (!m_PollCallback)
		{
			m_PollCallback = new MCPPollCallback(this);
		}
		MCPPollCallback cb = m_PollCallback;
		m_CallbackRefs.Insert(cb);
		string request = "poll?key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
		request = request + "&caps=" + EncodeQueryValue(SERVER_CAPABILITIES);
		request = request + "&ach=" + EncodeQueryValue(SERVER_ARG_CONTRACT_HASH);
		if (m_PeerInstance != "")
		{
			request = request + "&inst=" + EncodeQueryValue(m_PeerInstance);
		}
		m_Ctx.GET(cb, request);
	}

	void OnPollSuccess(string data, int dataSize)
	{
		if (!m_Configured)
		{
			return;
		}

		m_TickPollCallback = m_Tick;
		m_PollInFlight = false;

		MCPCommandBatch batch = new MCPCommandBatch();
		JsonSerializer serializer = new JsonSerializer();
		string parseError;
		bool parsed = serializer.ReadFromString(batch, data, parseError);
		if (!parsed)
		{
			Log("poll parse failed size=" + dataSize + " error=" + parseError);
			OnPollFail("parse_failed");
			return;
		}

		if (!batch.commands)
		{
			Log("poll returned null commands");
			OnPollFail("null_commands");
			return;
		}

		m_Backoff = 0.0;

		int count = batch.commands.Count();
		if (count > 0)
		{
			Log("poll commands=" + count + " sent_tick=" + m_TickPollSent + " callback_tick=" + m_TickPollCallback);
		}

		bool deferFromWorldSpawn = false;
		int i = 0;
		while (i < count)
		{
			MCPCommand command = batch.commands.Get(i);
			if (command && command.cmd == "world_spawn")
			{
				deferFromWorldSpawn = true;
				Log("world_spawn deferred id=" + command.id + " from=poll_callback callback_tick=" + m_TickPollCallback);
			}

			if (deferFromWorldSpawn)
			{
				QueuePendingOrFail(command);
			}
			else if (i < MAX_DISPATCH_PER_TICK)
			{
				Dispatch(command);
			}
			else
			{
				QueuePendingOrFail(command);
			}
			i = i + 1;
		}
	}

	protected void DrainPending()
	{
		if (!m_Pending)
		{
			return;
		}

		int dispatched = 0;
		while (m_Pending.Count() > 0 && dispatched < MAX_DISPATCH_PER_TICK)
		{
			MCPCommand command = m_Pending.Get(0);
			m_Pending.Remove(0);
			Dispatch(command);
			dispatched = dispatched + 1;
		}
	}

	protected void QueuePendingOrFail(MCPCommand command)
	{
		if (!command)
		{
			return;
		}

		if (!m_Pending)
		{
			return;
		}

		if (m_Pending.Count() >= MAX_PENDING)
		{
			PostCommandError(command, "bridge_queue_full");
			return;
		}

		m_Pending.Insert(command);
	}

	bool IsActivePollCallback(MCPPollCallback cb)
	{
		return cb == m_PollCallback;
	}

	void OnPollError(int errorCode)
	{
		// OnError may repeat (restapi.c:53); retire this request identity.
		m_PollCallback = null;
		OnPollFail("error=" + errorCode);
	}

	void OnPollTimeout()
	{
		m_PollCallback = null;
		OnPollFail("timeout");
	}

	protected void OnPollFail(string reason)
	{
		if (!m_Configured)
		{
			return;
		}

		m_PollInFlight = false;
		m_Accum = 0.0;

		if (m_Backoff <= 0.0)
		{
			m_Backoff = 1.0;
		}
		else
		{
			m_Backoff = m_Backoff * 2.0;
		}

		if (m_Backoff > 30.0)
		{
			m_Backoff = 30.0;
		}

		Log("poll " + reason + " backoff_s=" + m_Backoff);

		if (m_Backoff >= KEY_RELOAD_BACKOFF_S)
		{
			ReloadKeyAfterFailure();
		}
	}

	// The key is read once at configure time and never again, so rotating
	// it -- or a port reclaim handing the socket to a differently keyed holder --
	// leaves the bridge polling with a dead credential until the mission restarts.
	// The only visible symptom was the backoff above climbing to its 30 s cap.
	// The trigger is persistent failure, not a classified auth error, because the
	// callback receives an ERestResultState and EREST_ERROR shares its value with
	// EREST_ERROR_CLIENTERROR (restapi.c:16-17): a 401 and a refused connection are
	// indistinguishable here. Gated on the backoff so the file read costs at most
	// one per failed poll and never runs on the success path.
	// A changed url is deliberately NOT adopted: that needs a fresh RestContext,
	// which is init territory, not the poll failure path.
	protected void ReloadKeyAfterFailure()
	{
		string path = "$profile:dayz_mcp.json";
		if (!FileExist(path))
		{
			path = "$mission:dayz_mcp.json";
		}

		if (!FileExist(path))
		{
			return;
		}

		MCPConfig cfg = new MCPConfig();
		JsonFileLoader<MCPConfig>.JsonLoadFile(path, cfg);
		if (!cfg.key || cfg.key == "")
		{
			return;
		}

		if (cfg.key == m_Key)
		{
			return;
		}

		m_Key = cfg.key;
		m_Backoff = 0.0;
		Log("poll key reloaded path=" + path + " keylen=" + m_Key.Length());
	}

	protected void Dispatch(MCPCommand command)
	{
		if (!command)
		{
			return;
		}

		MCPResult result = new MCPResult();
		result.id = command.id;
		result.tick_poll_sent = m_TickPollSent;
		result.tick_poll_callback = m_TickPollCallback;
		result.tick_dispatch = m_Tick;
		bool postNow = true;

		if (command.cmd == "query_player_state")
		{
			MCPPlayerState state = BuildPlayerState();
			if (state && state.pos && state.pos.Count() == 3)
			{
				result.ok = true;
				result.state = state;
			}
			else
			{
				result.ok = false;
				result.error = "no_players";
			}
		}
		else if (command.cmd == "query_all_players")
		{
			result.players = BuildAllPlayers();
			result.ok = true;
		}
		else if (command.cmd == "world_spawn")
		{
			postNow = DispatchWorldSpawn(command, result);
			if (!postNow)
			{
				postNow = ApplySpawnLifetime(command, result);
			}
		}
		else if (command.cmd == "object_delete")
		{
			postNow = DispatchObjectDelete(command, result);
		}
		else if (command.cmd == "notify_players")
		{
			postNow = DispatchNotifyPlayers(command, result);
		}
		else if (command.cmd == "vehicle_enter")
		{
			postNow = DispatchVehicleEnter(command, result);
		}
		else if (command.cmd == "scene_raycast")
		{
			postNow = DispatchSceneRaycast(command, result);
		}
		else if (command.cmd == "telemetry_read")
		{
			postNow = DispatchTelemetryRead(command, result);
		}
		else if (command.cmd == "vehicle_prepare_fixture")
		{
			postNow = DispatchVehiclePrepareFixture(command, result);
		}
		else if (command.cmd == "surface_query")
		{
			postNow = DispatchSurfaceQuery(command, result);
		}
		else if (command.cmd == "player_teleport")
		{
			postNow = DispatchPlayerTeleport(command, result);
		}
		else if (command.cmd == "player_heal")
		{
			postNow = DispatchPlayerHeal(command, result);
		}
		else if (command.cmd == "player_godmode")
		{
			postNow = DispatchPlayerGodmode(command, result);
		}
		else if (command.cmd == "player_kill")
		{
			postNow = DispatchPlayerKill(command, result);
		}
		else if (command.cmd == "bot_start")
		{
			postNow = DispatchBotStart(command, result);
		}
		else if (command.cmd == "bot_stop")
		{
			postNow = DispatchBotStop(command, result);
		}
		else if (command.cmd == "object_anim")
		{
			postNow = DispatchObjectAnim(command, result);
		}
		else if (command.cmd == "vehicle_door")
		{
			postNow = DispatchVehicleDoor(command, result);
		}
		else if (command.cmd == "infected_drive")
		{
			postNow = DispatchInfectedDrive(command, result);
		}
		else if (command.cmd == "inventory_give")
		{
			postNow = DispatchInventoryGive(command, result);
		}
		else if (command.cmd == "inventory_attach")
		{
			postNow = DispatchInventoryAttach(command, result);
		}
		else if (command.cmd == "loadout_get")
		{
			postNow = DispatchLoadoutGet(command, result);
		}
		else if (command.cmd == "loadout_apply")
		{
			postNow = DispatchLoadoutApply(command, result);
		}
		else if (command.cmd == "loadout_clear")
		{
			postNow = DispatchLoadoutClear(command, result);
		}
		else if (command.cmd == "object_inspect")
		{
			postNow = DispatchObjectInspect(command, result);
		}
		else if (command.cmd == "object_resolve")
		{
			postNow = DispatchObjectResolve(command, result);
		}
		else if (command.cmd == "world_time_set")
		{
			postNow = DispatchWorldTimeSet(command, result);
		}
		else if (command.cmd == "world_time_get")
		{
			postNow = DispatchWorldTimeGet(command, result);
		}
		else if (command.cmd == "world_weather_set")
		{
			postNow = DispatchWorldWeatherSet(command, result);
		}
		else if (command.cmd == "exec_enforce")
		{
			postNow = DispatchExecEnforce(command, result);
		}
		else if (command.cmd == "query_get_in_condition")
		{
			postNow = DispatchQueryGetInCondition(command, result);
		}
		else if (command.cmd == "hands_take")
		{
			postNow = DispatchHandsTake(command, result);
		}
		else if (command.cmd == "hands_attach")
		{
			postNow = DispatchHandsAttach(command, result);
		}
		else if (command.cmd == "inventory_move")
		{
			postNow = DispatchInventoryMove(command, result);
		}
		else if (command.cmd == "object_doors")
		{
			postNow = DispatchObjectDoors(command, result);
		}
		else if (command.cmd == "weapon_state")
		{
			postNow = DispatchWeaponState(command, result);
		}
		else if (command.cmd == "action_cursor_ids")
		{
			postNow = DispatchActionCursorIds(command, result);
		}
		else if (command.cmd == "server_perf")
		{
			postNow = DispatchServerPerf(command, result);
		}
		else if (command.cmd == "nav_path_query")
		{
			postNow = DispatchNavPathQuery(command, result);
		}
		else if (command.cmd == "players_visibility")
		{
			postNow = DispatchPlayersVisibility(command, result);
		}
		else if (command.cmd == "entities_query")
		{
			postNow = DispatchEntitiesQuery(command, result);
		}
		else
		{
			result.ok = false;
			result.error = "unknown_command";
		}

		if (postNow)
		{
			PostResult(result);
		}
	}

	protected bool DispatchWorldSpawn(MCPCommand command, MCPResult result)
	{
		Log("spawn phase id=" + command.id + " phase=validate_begin");
		MCPSpawnValidation validation = ValidateSpawnArgs(command.args);
		Log("spawn phase id=" + command.id + " phase=validate_return");
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		Log("spawn phase id=" + command.id + " phase=create_begin");
		Object spawned = GetGame().CreateObjectEx(command.args.type, validation.pos, validation.flags, validation.rotation);
		Log("spawn phase id=" + command.id + " phase=create_return");
		if (!spawned)
		{
			result.ok = false;
			result.error = "spawn_failed";
			return true;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "spawn";
		job.args = command.args;
		job.subject = spawned;
		job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);
		m_RuntimeObjects.Insert(job.id, spawned);

		Log("job queued id=" + job.id + " kind=spawn deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool DispatchObjectDelete(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.object_id <= 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int objectId = command.args.object_id;
		result.object_id = objectId;
		result.deleted = 0;
		MCPBotControl.OnObjectGone(this, objectId);

		if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))
		{
			result.ok = true;
			return true;
		}

		Object target = m_RuntimeObjects.Get(objectId);
		m_RuntimeObjects.Remove(objectId);
		if (target)
		{
			GetGame().ObjectDelete(target);
			result.deleted = 1;
		}

		result.ok = true;
		return true;
	}

	// inventory_move (fb-20261024-212316-17fa). authority=client issues the
	// player's predictive call and confirms only from a later server read.
	// authority=server moves with ServerTake* or LocationSyncMoveEntity and
	// confirms from the read in this call when the location already matches.
	protected bool DispatchInventoryMove(MCPCommand command, MCPResult result)
	{
		result.accepted = false;
		result.confirmed = false;
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		int itemId = command.args.item_object_id;
		result.object_id = itemId;
		if (itemId <= 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		string op = command.args.op;
		if (!MCPInventoryMoveOpKnown(op))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		string authority = command.args.authority;
		if (authority == "")
		{
			authority = "client";
		}
		if (authority != "client")
		{
			if (authority != "server")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		if (op == "to_attachment")
		{
			if (command.args.slot == "")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		string itemError = "";
		EntityAI item = MCPInventoryMoveEntity(itemId, itemError);
		if (!item)
		{
			result.ok = false;
			result.error = itemError;
			return true;
		}
		result.type = item.GetType();
		EntityAI target = null;
		if (MCPInventoryMoveNeedsTarget(op))
		{
			int targetId = command.args.target_object_id;
			if (targetId <= 0)
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
			string targetError = "";
			target = MCPInventoryMoveEntity(targetId, targetError);
			if (!target)
			{
				result.ok = false;
				result.error = targetError;
				return true;
			}
		}
		PlayerBase player = null;
		// authority=server still needs the player when the item or the target
		// is in someone's inventory. Otherwise MCPInventoryMoveForeign treats a
		// null player as foreign to every player root, including this uid.
		// World to world leaves both roots null and does not resolve anyone.
		bool rootedOnPlayer = false;
		if (item.GetHierarchyRootPlayer())
		{
			rootedOnPlayer = true;
		}
		if (target && target.GetHierarchyRootPlayer())
		{
			rootedOnPlayer = true;
		}
		if (authority == "client" || MCPInventoryMoveNeedsPlayer(op) || rootedOnPlayer)
		{
			string playerError = "";
			player = ResolvePlayer(command.args, playerError);
			if (!player)
			{
				result.ok = false;
				result.error = playerError;
				return true;
			}
		}
		string foreignError = MCPInventoryMoveForeign(player, item);
		if (foreignError == "")
		{
			foreignError = MCPInventoryMoveForeign(player, target);
		}
		if (foreignError != "")
		{
			result.ok = false;
			result.error = foreignError;
			return true;
		}
		if (authority == "client")
		{
			string reachError = MCPInventoryMoveReach(player, item, target);
			if (reachError != "")
			{
				result.ok = false;
				result.error = reachError;
				return true;
			}
		}
		string roomError = MCPInventoryMoveRoom(player, item, target, op, command.args.slot);
		if (roomError != "")
		{
			result.ok = false;
			result.error = roomError;
			return true;
		}
		float beforeQty = MCPItemQuantity(item);
		if (op == "combine")
		{
			beforeQty = MCPItemQuantity(target);
		}
		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "inventory_move";
		job.args = command.args;
		job.args.op = op;
		job.args.authority = authority;
		job.subject = item;
		job.actor = player;
		job.move_target = target;
		job.move_quantity = beforeQty;
		job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		string performError = "";
		bool clientRpc = false;
		if (authority == "server")
		{
			performError = MCPServerInventoryMove(player, item, target, op, command.args.slot);
		}
		else if (!GetGame().IsMultiplayer())
		{
			performError = player.MCPPerformClientInventoryMove(op, item, target, command.args.slot);
		}
		else
		{
			// A listen server can deliver the refusal RPC before Send returns.
			// The job has to be in m_Jobs first. The handler only sets phase.
			// Dispatch or ProcessJobs posts the one result.
			clientRpc = true;
			m_Jobs.Insert(job.id, job);
			performError = MCPSendInventoryMove(player, item, target, op, command.args.slot, job.id);
		}
		if (performError != "")
		{
			if (clientRpc && m_Jobs.Contains(job.id))
			{
				m_Jobs.Remove(job.id);
			}
			result.ok = false;
			result.error = performError;
			return true;
		}
		if (clientRpc && job.phase == 1)
		{
			MCPWriteInventoryMove(result, job, true, "");
			result.ok = true;
			m_Jobs.Remove(job.id);
			return true;
		}
		result.accepted = true;
		if (MCPInventoryMoveReady(job))
		{
			MCPWriteInventoryMove(result, job, true, "");
			result.ok = true;
			if (clientRpc)
			{
				m_Jobs.Remove(job.id);
			}
			return true;
		}
		// The server read wins: a client refusal is posted only when the move
		// is not visible yet.
		if (clientRpc && job.phase == 2)
		{
			MCPPostInventoryMoveRefused(job, job.error);
			m_Jobs.Remove(job.id);
			return false;
		}
		if (!clientRpc)
		{
			m_Jobs.Insert(job.id, job);
		}
		return false;
	}

	protected bool MCPInventoryMoveOpKnown(string op)
	{
		if (op == "to_cargo")
		{
			return true;
		}
		if (op == "to_attachment")
		{
			return true;
		}
		if (op == "to_hands")
		{
			return true;
		}
		if (op == "to_inventory")
		{
			return true;
		}
		if (op == "combine")
		{
			return true;
		}
		if (op == "split_half")
		{
			return true;
		}
		if (op == "split_to_hands")
		{
			return true;
		}
		return false;
	}

	protected bool MCPInventoryMoveNeedsTarget(string op)
	{
		if (op == "to_cargo")
		{
			return true;
		}
		if (op == "to_attachment")
		{
			return true;
		}
		if (op == "combine")
		{
			return true;
		}
		return false;
	}

	protected bool MCPInventoryMoveNeedsPlayer(string op)
	{
		if (op == "to_hands")
		{
			return true;
		}
		if (op == "to_inventory")
		{
			return true;
		}
		if (op == "split_half")
		{
			return true;
		}
		if (op == "split_to_hands")
		{
			return true;
		}
		return false;
	}

	protected EntityAI MCPInventoryMoveEntity(int objectId, out string error)
	{
		error = "";
		if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))
		{
			error = "object_not_found";
			return null;
		}
		Object registered = m_RuntimeObjects.Get(objectId);
		if (!registered)
		{
			error = "object_not_found";
			return null;
		}
		EntityAI entity = EntityAI.Cast(registered);
		if (!entity)
		{
			error = "not_an_item";
			return null;
		}
		return entity;
	}

	// Worn or carried by a player who is not the selected one: never moved.
	protected string MCPInventoryMoveForeign(PlayerBase player, EntityAI entity)
	{
		if (!entity)
		{
			return "";
		}
		Man root = entity.GetHierarchyRootPlayer();
		if (!root)
		{
			return "";
		}
		if (player && root == player)
		{
			return "";
		}
		return "not_reachable";
	}

	protected bool MCPInventoryMoveNear(PlayerBase player, EntityAI entity)
	{
		if (!player || !entity)
		{
			return false;
		}
		Man root = entity.GetHierarchyRootPlayer();
		if (root)
		{
			if (root == player)
			{
				return true;
			}
		}
		float reach = UAMaxDistances.DEFAULT;
		float reachSq = reach * reach;
		float distSq = vector.DistanceSq(player.GetPosition(), entity.GetPosition());
		if (distSq > reachSq)
		{
			return false;
		}
		return true;
	}

	protected string MCPInventoryMoveReach(PlayerBase player, EntityAI item, EntityAI target)
	{
		if (!MCPInventoryMoveNear(player, item))
		{
			return "not_reachable";
		}
		if (target)
		{
			if (!MCPInventoryMoveNear(player, target))
			{
				return "not_reachable";
			}
		}
		return "";
	}

	protected string MCPInventoryMoveRoom(PlayerBase player, EntityAI item, EntityAI target, string op, string slotName)
	{
		if (op == "to_cargo")
		{
			if (!target || !target.GetInventory())
			{
				return "inventory_unavailable";
			}
			InventoryLocation cargoLoc = new InventoryLocation;
			if (!target.GetInventory().FindFreeLocationFor(item, FindInventoryLocationType.ANY_CARGO, cargoLoc))
			{
				return "no_room";
			}
		}
		if (op == "to_attachment")
		{
			if (!target || !target.GetInventory())
			{
				return "inventory_unavailable";
			}
			int slotId = InventorySlots.GetSlotIdFromString(slotName);
			if (!InventorySlots.IsSlotIdValid(slotId))
			{
				return "slot_not_found";
			}
			if (!target.GetInventory().HasAttachmentSlot(slotId))
			{
				return "slot_not_found";
			}
			if (target.GetInventory().FindAttachment(slotId))
			{
				return "slot_occupied";
			}
		}
		if (op == "combine")
		{
			ItemBase combineReceiver = ItemBase.Cast(target);
			ItemBase combineSource = ItemBase.Cast(item);
			if (!combineReceiver)
			{
				return "not_an_item";
			}
			if (!combineSource)
			{
				return "not_an_item";
			}
			if (!combineReceiver.CanBeCombined(combineSource, false))
			{
				return "no_room";
			}
		}
		if (op == "split_half" || op == "split_to_hands")
		{
			ItemBase stack = ItemBase.Cast(item);
			if (!stack)
			{
				return "not_an_item";
			}
			if (!stack.CanBeSplit())
			{
				return "no_room";
			}
		}
		// Same flags as PredictiveTakeEntityToInventory and ServerTakeEntityToInventory.
		// ANY includes HANDS. The confirmation accepts that when the parent is the player.
		if (op == "to_inventory")
		{
			if (!player || !player.GetInventory())
			{
				return "inventory_unavailable";
			}
			InventoryLocation invLoc = new InventoryLocation;
			if (!player.GetInventory().FindFreeLocationFor(item, FindInventoryLocationType.ANY, invLoc))
			{
				return "no_room";
			}
		}
		return "";
	}

	protected float MCPItemQuantity(EntityAI entity)
	{
		ItemBase item = ItemBase.Cast(entity);
		if (!item)
		{
			return 0;
		}
		Magazine mag = Magazine.Cast(entity);
		if (mag)
		{
			return mag.GetAmmoCount();
		}
		return item.GetQuantity();
	}

	protected string MCPSendInventoryMove(PlayerBase player, EntityAI item, EntityAI target, string op, string slotName, int jobId)
	{
		int itemLow = 0;
		int itemHigh = 0;
		item.GetNetworkID(itemLow, itemHigh);
		if (itemLow == 0 && itemHigh == 0)
		{
			return "not_networked";
		}
		if (target)
		{
			int targetLow = 0;
			int targetHigh = 0;
			target.GetNetworkID(targetLow, targetHigh);
			if (targetLow == 0 && targetHigh == 0)
			{
				return "not_networked";
			}
		}
		if (!player.GetIdentity())
		{
			return "no_identity";
		}
		string targetType = "";
		if (target)
		{
			targetType = target.GetType();
		}
		if (!player.MCPRequestInventoryMove(jobId, item, item.GetType(), target, targetType, op, slotName))
		{
			return "rpc_failed";
		}
		return "";
	}

	protected string MCPServerInventoryMove(PlayerBase player, EntityAI item, EntityAI target, string op, string slotName)
	{
		GameInventory itemInventory = item.GetInventory();
		if (!itemInventory)
		{
			return "inventory_unavailable";
		}
		InventoryLocation src = new InventoryLocation;
		if (!itemInventory.GetCurrentInventoryLocation(src))
		{
			return "not_reachable";
		}
		if (op == "to_cargo")
		{
			InventoryLocation dst = new InventoryLocation;
			if (!target.GetInventory().FindFreeLocationFor(item, FindInventoryLocationType.ANY_CARGO, dst))
			{
				return "no_room";
			}
			if (!GameInventory.LocationSyncMoveEntity(src, dst))
			{
				return "no_room";
			}
			return "";
		}
		if (op == "to_attachment")
		{
			int slotId = InventorySlots.GetSlotIdFromString(slotName);
			InventoryLocation dstAtt = new InventoryLocation;
			dstAtt.SetAttachment(target, item, slotId);
			if (!GameInventory.LocationSyncMoveEntity(src, dstAtt))
			{
				return "no_room";
			}
			return "";
		}
		if (op == "to_hands")
		{
			player.ServerTakeEntityToHands(item);
			return "";
		}
		if (op == "to_inventory")
		{
			if (!player.ServerTakeEntityToInventory(FindInventoryLocationType.ANY, item))
			{
				return "no_room";
			}
			return "";
		}
		if (op == "combine")
		{
			ItemBase receiver = ItemBase.Cast(target);
			ItemBase source = ItemBase.Cast(item);
			if (!receiver)
			{
				return "not_an_item";
			}
			if (!source)
			{
				return "not_an_item";
			}
			receiver.CombineItems(source, true);
			return "";
		}
		if (op == "split_half")
		{
			ItemBase half = ItemBase.Cast(item);
			if (!half)
			{
				return "not_an_item";
			}
			half.SplitItem(player);
			return "";
		}
		if (op == "split_to_hands")
		{
			ItemBase toHands = ItemBase.Cast(item);
			if (!toHands)
			{
				return "not_an_item";
			}
			toHands.SplitIntoStackMaxHands(player);
			return "";
		}
		return "bad_args";
	}

	protected bool MCPInventoryMoveReady(MCPJob job)
	{
		if (!job || !job.args)
		{
			return false;
		}
		string op = job.args.op;
		EntityAI item = EntityAI.Cast(job.subject);
		if (op == "combine")
		{
			if (MCPItemQuantity(EntityAI.Cast(job.move_target)) > job.move_quantity + 0.001)
			{
				return true;
			}
			return false;
		}
		if (op == "split_half")
		{
			if (!item)
			{
				return false;
			}
			if (MCPItemQuantity(item) + 0.001 < job.move_quantity)
			{
				return true;
			}
			return false;
		}
		if (op == "split_to_hands")
		{
			if (!item)
			{
				return false;
			}
			if (MCPItemQuantity(item) + 0.001 < job.move_quantity)
			{
				return true;
			}
			return false;
		}
		if (!item || !item.GetInventory())
		{
			return false;
		}
		InventoryLocation loc = new InventoryLocation;
		if (!item.GetInventory().GetCurrentInventoryLocation(loc))
		{
			return false;
		}
		int locType = loc.GetType();
		EntityAI parent = loc.GetParent();
		// to_cargo, to_attachment and combine ignore job.actor. Resolving a
		// player for a rooted item does not change these three verdicts.
		if (op == "to_cargo")
		{
			if (parent != job.move_target)
			{
				return false;
			}
			if (locType == InventoryLocationType.CARGO)
			{
				return true;
			}
			if (locType == InventoryLocationType.PROXYCARGO)
			{
				return true;
			}
			return false;
		}
		if (op == "to_attachment")
		{
			if (parent != job.move_target)
			{
				return false;
			}
			if (locType != InventoryLocationType.ATTACHMENT)
			{
				return false;
			}
			string slotName = InventorySlots.GetSlotName(loc.GetSlot());
			if (slotName != job.args.slot)
			{
				return false;
			}
			return true;
		}
		if (op == "to_hands")
		{
			if (locType != InventoryLocationType.HANDS)
			{
				return false;
			}
			if (parent != job.actor)
			{
				return false;
			}
			return true;
		}
		if (op == "to_inventory")
		{
			// ANY includes HANDS, so a move into this player's hands is confirmed.
			if (locType == InventoryLocationType.HANDS)
			{
				if (parent == job.actor)
				{
					return true;
				}
				return false;
			}
			if (locType == InventoryLocationType.GROUND)
			{
				return false;
			}
			Man root = item.GetHierarchyRootPlayer();
			if (root != job.actor)
			{
				return false;
			}
			return true;
		}
		return false;
	}

	protected void MCPWriteInventoryMove(MCPResult result, MCPJob job, bool settled, string errorText)
	{
		result.accepted = true;
		result.confirmed = settled;
		if (settled)
		{
			result.ok = true;
			result.error = "";
		}
		else
		{
			result.ok = false;
			result.error = errorText;
		}
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		if (job.args)
		{
			result.object_id = job.args.item_object_id;
		}
		EntityAI item = EntityAI.Cast(job.subject);
		MCPInventoryMove report = new MCPInventoryMove();
		if (job.args)
		{
			report.op = job.args.op;
			report.authority = job.args.authority;
		}
		report.loc_type = "missing";
		report.parent_object_id = 0;
		report.parent_type = "";
		report.slot = "";
		report.quantity = 0;
		if (item)
		{
			result.type = item.GetType();
			report.quantity = MCPItemQuantity(item);
			GameInventory inventory = item.GetInventory();
			if (inventory)
			{
				InventoryLocation loc = new InventoryLocation;
				if (inventory.GetCurrentInventoryLocation(loc))
				{
					report.loc_type = typename.EnumToString(InventoryLocationType, loc.GetType());
					EntityAI parent = loc.GetParent();
					if (parent)
					{
						report.parent_object_id = RuntimeObjectId(parent);
						report.parent_type = parent.GetType();
					}
					if (loc.GetType() == InventoryLocationType.ATTACHMENT)
					{
						report.slot = InventorySlots.GetSlotName(loc.GetSlot());
					}
				}
			}
		}
		result.inventory_move = report;
	}

	protected void MCPPostInventoryMove(MCPJob job, bool settled, string errorText)
	{
		MCPResult result = new MCPResult();
		MCPWriteInventoryMove(result, job, settled, errorText);
		PostResult(result);
	}

	// Confirm and timeout keep accepted=true through MCPWriteInventoryMove.
	// A client refusal is a separate result: the call was not accepted.
	protected void MCPPostInventoryMoveRefused(MCPJob job, string reason)
	{
		MCPResult result = new MCPResult();
		MCPWriteInventoryMove(result, job, false, reason);
		result.ok = false;
		result.accepted = false;
		result.confirmed = false;
		result.error = reason;
		PostResult(result);
	}

	protected string MCPInventoryMoveClientReason(string reason)
	{
		if (reason == "not_client")
		{
			return reason;
		}
		if (reason == "object_not_found")
		{
			return reason;
		}
		if (reason == "input_busy")
		{
			return reason;
		}
		if (reason == "not_reachable")
		{
			return reason;
		}
		if (reason == "slot_not_found")
		{
			return reason;
		}
		if (reason == "no_room")
		{
			return reason;
		}
		if (reason == "not_an_item")
		{
			return reason;
		}
		if (reason == "bad_args")
		{
			return reason;
		}
		if (reason == "not_streamed")
		{
			return reason;
		}
		if (reason == "type_mismatch")
		{
			return reason;
		}
		if (reason == "read_failed")
		{
			return reason;
		}
		return "client_refused";
	}

	// Client to server. Unrelated RPC types stay quiet. A failed check after
	// the type matches is ignored and the job keeps its deadline.
	void MCPOnInventoryMoveRefused(PlayerIdentity sender, Object target, int rpc_type, ParamsReadContext ctx)
	{
		if (rpc_type != MCP_RPC_INVENTORY_MOVE_REFUSED)
		{
			return;
		}
		if (!GetGame() || !GetGame().IsServer() || !GetGame().IsMultiplayer())
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		int jobId = 0;
		string reason = "";
		if (!ctx.Read(jobId))
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		if (!ctx.Read(reason))
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		MCPJob job = m_Jobs.Get(jobId);
		if (!job || job.kind != "inventory_move" || !job.args || job.args.authority != "client")
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		if (!job.actor || target != job.actor || !sender || !job.actor.GetIdentity())
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		if (sender.GetId() != job.actor.GetIdentity().GetId())
		{
			Print("[DayZ_MCP] inventory_move refusal ignored");
			return;
		}
		reason = MCPInventoryMoveClientReason(reason);
		// Publishing here would be a write the census can reach from every
		// command, because Event_OnRPC is not a command. The job pass posts it.
		// phase 1: the server read already matches, so confirmation wins.
		// phase 2: one refusal, and the deadline must not post a second result.
		if (MCPInventoryMoveReady(job))
		{
			job.phase = 1;
		}
		else
		{
			job.phase = 2;
			job.error = reason;
		}
	}

	// hands_take validates on the server (object_id lives in m_RuntimeObjects:
	// world_spawn and inventory_attach register there) and asks the owning
	// client to call PredictiveTakeEntityToHands. That call
	// no-ops on a dedicated server (actiontakeitemtohands.c OnExecute returns
	// before it). accepted is the synchronous verdict; confirmed stays false.
	protected bool DispatchHandsTake(MCPCommand command, MCPResult result)
	{
		result.accepted = false;
		result.confirmed = false;
		if (!command.args || command.args.object_id <= 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int objectId = command.args.object_id;
		result.object_id = objectId;
		if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))
		{
			result.ok = false;
			result.error = "object_id_unknown";
			return true;
		}

		Object registered = m_RuntimeObjects.Get(objectId);
		if (!registered)
		{
			result.ok = false;
			result.error = "object_id_stale";
			return true;
		}

		result.type = registered.GetType();
		ItemBase item = ItemBase.Cast(registered);
		if (!item)
		{
			result.ok = false;
			result.error = "not_an_item";
			return true;
		}

		string playerError = "";
		PlayerBase player = ResolvePlayer(command.args, playerError);
		if (!player)
		{
			result.ok = false;
			result.error = playerError;
			return true;
		}

		// Predicates live on PlayerBase so the client RPC recheck is the same
		// function, not a second copy. validated type is result.type.
		string refusal = player.MCPHandsTakeRefusal(item);
		if (refusal != "")
		{
			result.ok = false;
			result.error = refusal;
			return true;
		}

		if (!GetGame().IsMultiplayer())
		{
			if (!ScriptInputUserData.CanStoreInputUserData())
			{
				result.ok = false;
				result.error = "input_busy";
				return true;
			}
			player.PredictiveTakeEntityToHands(item);
			result.ok = true;
			result.accepted = true;
			result.confirmed = false;
			return true;
		}

		int netLow = 0;
		int netHigh = 0;
		item.GetNetworkID(netLow, netHigh);
		bool networked = true;
		if (netLow == 0)
		{
			if (netHigh == 0)
			{
				networked = false;
			}
		}
		if (!networked)
		{
			result.ok = false;
			result.error = "not_networked";
			return true;
		}
		if (!player.GetIdentity())
		{
			result.ok = false;
			result.error = "no_identity";
			return true;
		}

		if (!player.MCPRequestTakeToHands(item, result.type))
		{
			result.ok = false;
			result.error = "rpc_failed";
			return true;
		}
		result.ok = true;
		result.accepted = true;
		result.confirmed = false;
		return true;
	}

	// hands_attach (fb-20261011-215405-9608) puts the item in a player's hands
	// into one attachment slot of a registered world entity, the same object,
	// so its registry id and state (a CarBattery's energy) carry over. Same
	// shape as hands_take: the server checks, the owning client runs
	// PredictiveTakeEntityToTargetAttachmentEx, accepted is the synchronous
	// verdict and confirmed stays false. object_id is the destination;
	// inventory_attach.item_object_id is the held item's registry id, 0 when
	// it was never registered.
	protected bool DispatchHandsAttach(MCPCommand command, MCPResult result)
	{
		result.accepted = false;
		result.confirmed = false;
		if (!command.args || command.args.object_id <= 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (command.args.slot == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int objectId = command.args.object_id;
		result.object_id = objectId;
		if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))
		{
			result.ok = false;
			result.error = "object_id_unknown";
			return true;
		}

		Object registered = m_RuntimeObjects.Get(objectId);
		if (!registered)
		{
			result.ok = false;
			result.error = "object_id_stale";
			return true;
		}

		EntityAI target = EntityAI.Cast(registered);
		if (!target)
		{
			result.ok = false;
			result.error = "not_entity_ai";
			return true;
		}

		string playerError = "";
		PlayerBase player = ResolvePlayer(command.args, playerError);
		if (!player)
		{
			result.ok = false;
			result.error = playerError;
			return true;
		}

		EntityAI held = player.GetEntityInHands();
		if (held)
		{
			result.type = held.GetType();
		}
		int slotId = InventorySlots.GetSlotIdFromString(command.args.slot);
		string refusal = player.MCPHandsAttachRefusal(target, slotId);
		if (refusal != "")
		{
			result.ok = false;
			result.error = refusal;
			return true;
		}

		MCPInventoryAttachReceipt attachReceipt = new MCPInventoryAttachReceipt();
		attachReceipt.dest = "attachment";
		attachReceipt.slot = command.args.slot;
		attachReceipt.item_object_id = RuntimeObjectId(held);
		result.inventory_attach = attachReceipt;

		if (!GetGame().IsMultiplayer())
		{
			if (!ScriptInputUserData.CanStoreInputUserData())
			{
				result.ok = false;
				result.error = "input_busy";
				return true;
			}
			player.PredictiveTakeEntityToTargetAttachmentEx(target, held, slotId);
			result.ok = true;
			result.accepted = true;
			result.confirmed = false;
			return true;
		}

		int netLow = 0;
		int netHigh = 0;
		target.GetNetworkID(netLow, netHigh);
		if (netLow == 0)
		{
			if (netHigh == 0)
			{
				result.ok = false;
				result.error = "not_networked";
				return true;
			}
		}
		held.GetNetworkID(netLow, netHigh);
		if (netLow == 0)
		{
			if (netHigh == 0)
			{
				result.ok = false;
				result.error = "not_networked";
				return true;
			}
		}
		if (!player.GetIdentity())
		{
			result.ok = false;
			result.error = "no_identity";
			return true;
		}

		if (!player.MCPRequestAttachFromHands(target, target.GetType(), held, result.type, command.args.slot))
		{
			result.ok = false;
			result.error = "rpc_failed";
			return true;
		}
		result.ok = true;
		result.accepted = true;
		result.confirmed = false;
		return true;
	}

	// Registry id of this object (world_spawn or inventory_attach), or 0 when it
	// is not in m_RuntimeObjects. The id is this run only.
	// map.GetElement / GetKey are O(n) (enscript.c:868, :878); foreach is the
	// linear walk vanilla uses (effectmanager.c:547).
	protected int RuntimeObjectId(Object subject)
	{
		if (!subject)
		{
			return 0;
		}
		if (!m_RuntimeObjects)
		{
			return 0;
		}
		foreach (int spawnedId, Object registered : m_RuntimeObjects)
		{
			if (registered == subject)
			{
				return spawnedId;
			}
		}
		return 0;
	}

	// Resolve captured network pairs against the run registry. Does not Insert.
	protected bool DispatchActionCursorIds(MCPCommand command, MCPResult result)
	{
		int count;
		int index;
		int low;
		int high;
		Object resolved;
		MCPArgs args;
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		args = command.args;
		if (!args.net_low || !args.net_high)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		count = args.net_low.Count();
		if (count != args.net_high.Count() || count > 16)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		result.cursor_ids = new MCPCursorIds();
		result.cursor_ids.run_token = args.run_token;
		index = 0;
		while (index < count)
		{
			low = args.net_low.Get(index);
			high = args.net_high.Get(index);
			resolved = null;
			if (GetGame())
			{
				resolved = GetGame().GetObjectByNetworkId(low, high);
			}
			result.cursor_ids.object_ids.Insert(RuntimeObjectId(resolved));
			index = index + 1;
		}
		result.ok = true;
		return true;
	}

	// Server read. "Local player" on this peer is ResolvePlayer: empty uid is
	// the first human. GetGame().GetPlayer() is null on a dedicated server.
	protected bool DispatchWeaponState(MCPCommand command, MCPResult result)
	{
		result.accepted = false;
		result.confirmed = false;
		string playerError = "";
		PlayerBase player = ResolvePlayer(command.args, playerError);
		if (!player)
		{
			result.ok = false;
			result.error = playerError;
			result.found = false;
			result.type = "";
			return true;
		}

		EntityAI held = player.GetEntityInHands();
		if (!held)
		{
			// Completed read. ok=false would become ToolError and drop type.
			result.ok = true;
			result.error = "no_weapon_in_hands";
			result.found = false;
			result.type = "";
			result.object_id = 0;
			return true;
		}

		result.object_id = RuntimeObjectId(held);
		Weapon_Base heldWeapon = Weapon_Base.Cast(held);
		if (!heldWeapon)
		{
			result.ok = true;
			result.error = "no_weapon_in_hands";
			result.found = false;
			result.type = held.GetType();
			return true;
		}

		result.weapon_state = BuildWeaponState(heldWeapon);
		result.type = heldWeapon.GetType();
		result.found = true;
		result.ok = true;
		result.error = "";
		return true;
	}

	protected MCPWeaponState BuildWeaponState(Weapon_Base heldWeapon)
	{
		MCPWeaponState state = new MCPWeaponState();
		int muzzleCount = heldWeapon.GetMuzzleCount();
		int current = heldWeapon.GetCurrentMuzzle();
		state.muzzle_index = current;
		state.jammed = heldWeapon.IsJammed();
		state.shots = heldWeapon.MCPShotCount();
		if (current < 0 || current >= muzzleCount)
		{
			state.mode_index = -1;
			state.mode_name = "";
		}
		else
		{
			state.mode_index = heldWeapon.GetCurrentMode(current);
			state.mode_name = heldWeapon.GetCurrentModeName(current);
		}

		int i = 0;
		while (i < muzzleCount)
		{
			MCPWeaponMuzzleState muzzle = new MCPWeaponMuzzleState();
			muzzle.index = i;
			muzzle.chamber_empty = heldWeapon.IsChamberEmpty(i);
			muzzle.chamber_fired_out = heldWeapon.IsChamberFiredOut(i);
			muzzle.internal_cartridges = heldWeapon.GetInternalMagazineCartridgeCount(i);
			Magazine mag = heldWeapon.GetMagazine(i);
			if (mag)
			{
				muzzle.magazine_present = true;
				muzzle.magazine_ammo = mag.GetAmmoCount();
			}
			else
			{
				muzzle.magazine_present = false;
				muzzle.magazine_ammo = 0;
			}
			state.muzzles.Insert(muzzle);
			i = i + 1;
		}
		return state;
	}

	protected bool DispatchNotifyPlayers(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.show_time <= 0.0 || !IsFiniteFloat(command.args.show_time) || command.args.title == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		PlayerIdentity targetIdentity = null;
		if (command.args.uid != "")
		{
			Human human = FindHumanByUid(command.args.uid);
			if (human)
			{
				targetIdentity = human.GetIdentity();
			}

			if (!targetIdentity)
			{
				result.ok = false;
				result.error = "player_not_found";
				return true;
			}
		}

		NotificationSystem.SendNotificationToPlayerIdentityExtended(targetIdentity, command.args.show_time, command.args.title, command.args.detail, command.args.icon);
		result.ok = true;
		result.sent = true;
		return true;
	}

	protected bool DispatchVehicleEnter(MCPCommand command, MCPResult result)
	{
		MCPSpawnValidation validation = ValidatePositionArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		Human human = GetFirstHuman();
		if (!human)
		{
			result.ok = false;
			result.error = "no_players";
			return true;
		}

		Transport vehicle = FindTransportNear(validation.pos);
		if (!vehicle)
		{
			result.ok = false;
			result.error = "no_vehicle";
			return true;
		}

		if (HasActiveJobFor(human, vehicle))
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		int crew = 0;
		int seatAnim = vehicle.GetSeatAnimationType(crew);
		HumanCommandVehicle vehicleCommand = human.StartCommand_Vehicle(vehicle, crew, seatAnim);
		if (!vehicleCommand)
		{
			result.ok = false;
			result.error = "seat_failed";
			return true;
		}

		vehicleCommand.SetVehicleType(vehicle.GetAnimInstance());

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "seat";
		job.args = command.args;
		job.subject = vehicle;
		job.actor = human;
		job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);

		Log("job queued id=" + job.id + " kind=seat deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool DispatchQueryGetInCondition(MCPCommand command, MCPResult result)
	{
		MCPSpawnValidation validation = ValidatePositionArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		Transport trans = FindTransportNear(validation.pos);
		if (!trans)
		{
			result.ok = false;
			result.error = "no_vehicle";
			return true;
		}

		Human human = GetFirstHuman();
		PlayerBase player = PlayerBase.Cast(human);
		EntityAI itemInHands = null;
		MCPGetInCondition gi = new MCPGetInCondition();
		result.get_in = gi;
		gi.available = false;
		gi.partial = false;
		gi.crew_size = trans.CrewSize();
		gi.component_crew_index = -1;
		gi.first_block = "";
		result.ok = true;

		if (player)
		{
			itemInHands = player.GetItemInHands();
			if (itemInHands && itemInHands.IsHeavyBehaviour())
			{
				gi.first_block = "item_heavy";
				return true;
			}

			if (player.GetCommand_Vehicle())
			{
				gi.first_block = "already_in_vehicle";
				return true;
			}
		}

		int component = command.args.component;
		int crewIndex = -1;
		int crewIndexLoop = 0;
		MCPSeatCondition sc = null;
		Human occupant = null;
		bool occupied = false;
		bool through = false;
		bool areaFree = false;
		bool reachable = false;
		array<string> selections = null;
		int selectionIndex = 0;
		vector fromPos = "0 0 0";
		string selectionName = "";

		if (component >= 0)
		{
			gi.partial = false;
			crewIndex = trans.CrewPositionIndex(component);
			gi.component_crew_index = crewIndex;

			if (crewIndex < 0)
			{
				gi.first_block = "componentNN";
				gi.available = false;
				return true;
			}

			occupant = trans.CrewMember(crewIndex);
			occupied = occupant != null;
			through = trans.CrewCanGetThrough(crewIndex);
			areaFree = trans.IsAreaAtDoorFree(crewIndex);
			reachable = false;

			if (player)
			{
				selections = new array<string>();
				trans.GetActionComponentNameList(component, selections);
				fromPos = player.GetPosition();
				selectionIndex = 0;
				while (selectionIndex < selections.Count())
				{
					selectionName = selections.Get(selectionIndex);
					if (trans.CanReachSeatFromDoors(selectionName, fromPos, 1.0))
					{
						reachable = true;
						break;
					}

					selectionIndex = selectionIndex + 1;
				}
			}

			sc = new MCPSeatCondition();
			sc.crew_index = crewIndex;
			sc.occupied = occupied;
			sc.crew_can_get_through = through;
			sc.area_free = areaFree;
			sc.reachable = reachable;
			gi.per_seat.Insert(sc);

			if (occupied)
			{
				gi.first_block = "occupied";
			}
			else if (!through)
			{
				gi.first_block = "crew_can_get_through";
			}
			else if (!areaFree)
			{
				gi.first_block = "area_blocked";
			}
			else if (!reachable)
			{
				gi.first_block = "unreachable";
			}
			else
			{
				gi.first_block = "";
				gi.available = true;
			}

			return true;
		}

		gi.partial = true;
		gi.available = false;
		gi.first_block = "no_component";
		crewIndexLoop = 0;
		while (crewIndexLoop < gi.crew_size)
		{
			sc = new MCPSeatCondition();
			occupant = trans.CrewMember(crewIndexLoop);
			sc.crew_index = crewIndexLoop;
			sc.occupied = occupant != null;
			sc.crew_can_get_through = trans.CrewCanGetThrough(crewIndexLoop);
			sc.area_free = trans.IsAreaAtDoorFree(crewIndexLoop);
			sc.reachable = false;
			gi.per_seat.Insert(sc);
			crewIndexLoop = crewIndexLoop + 1;
		}

		return true;
	}

	protected bool DispatchSceneRaycast(MCPCommand command, MCPResult result)
	{
		MCPRaycastValidation validation = ValidateRaycastArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		string method = command.args.method;
		if (method == "")
		{
			method = "rvproxy";
		}

		Object ignore = ResolveRaycastIgnore(command.args);
		MCPRaycastHit hit = new MCPRaycastHit();
		hit.method = method;

		if (method == "rvproxy")
		{
			PopulateRaycastRVProxy(validation, ignore, hit);
		}
		else
		{
			PopulateRaycastBullet(validation, ignore, hit);
		}

		result.ok = true;
		result.raycast = hit;
		return true;
	}

	protected bool DispatchTelemetryRead(MCPCommand command, MCPResult result)
	{
		MCPTelemetryValidation validation = ValidateTelemetryArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		MCPTelemetry telemetry = new MCPTelemetry();
		telemetry.mode = validation.mode;
		telemetry.path = validation.path;

		if (validation.mode == "object_at")
		{
			return DispatchTelemetryObjectAt(validation, telemetry, result);
		}

		return DispatchTelemetryFixtureJsonl(validation, telemetry, result);
	}

	protected bool DispatchVehiclePrepareFixture(MCPCommand command, MCPResult result)
	{
		// F3.2: classname is free; CarScript.Cast below rejects non-vehicles.
		if (!command.args || command.args.mode != "object_at" || command.args.type == "" || !command.args.pos || command.args.pos.Count() != 3 || command.args.radius <= 0.0 || !IsFiniteFloat(command.args.radius))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		MCPTelemetryValidation validation = ValidateTelemetryArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		MCPTelemetry telemetry = new MCPTelemetry();
		telemetry.mode = validation.mode;
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(validation.pos, validation.radius, m_ReadyObjects, m_ReadyProxyCargos);

		Object match = null;
		int matchCount = 0;
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found && found.GetType() == validation.type)
			{
				matchCount = matchCount + 1;
				if (matchCount == 1)
				{
					match = found;
				}
			}

			i = i + 1;
		}

		if (matchCount == 0)
		{
			telemetry.found = false;
			result.ok = false;
			result.error = "fixture_not_found";
			result.telemetry = telemetry;
			return true;
		}

		if (matchCount > 1)
		{
			result.ok = false;
			result.error = "ambiguous_fixture";
			result.telemetry = telemetry;
			return true;
		}

		CarScript car = CarScript.Cast(match);
		if (!car)
		{
			result.ok = false;
			result.error = "fixture_not_vehicle";
			result.telemetry = telemetry;
			return true;
		}

		if (!IsVehicleFixtureReady(car))
		{
			car.OnDebugSpawn();
		}

		PopulateTelemetryObject(car, telemetry);
		result.telemetry = telemetry;
		result.vehicle_fixture_ready = IsVehicleFixtureReady(car);
		if (!result.vehicle_fixture_ready)
		{
			result.ok = false;
			result.error = "fixture_not_ready";
			return true;
		}

		result.ok = true;
		return true;
	}

	// F3.1: surface under (x,z). y is the return of SurfaceGetType (hit Y + out type);
	// SurfaceY is not used because SurfaceGetType already supplies a coherent Y+type pair.
	// Failure: non-finite x/z -> bad_args; outside [0, worldSize] -> out_of_bounds.
	// The Surface* API has no off-map signal, so world bounds are the only stable gate.
	protected bool DispatchSurfaceQuery(MCPCommand command, MCPResult result)
	{
		if (!command.args || !IsFiniteFloat(command.args.x) || !IsFiniteFloat(command.args.z))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		World world = GetGame().GetWorld();
		if (!world)
		{
			result.ok = false;
			result.error = "world_unavailable";
			return true;
		}

		float worldSize = world.GetWorldSize();
		if (!IsFiniteWorldCoord(command.args.x, worldSize) || !IsFiniteWorldCoord(command.args.z, worldSize))
		{
			result.ok = false;
			result.error = "out_of_bounds";
			return true;
		}

		string surfaceType = "";
		// SurfaceGetType is not proto native; returns hit Y and fills type by out-param.
		// game.c:1166 — do not treat the return as "success bool".
		float surfaceY = GetGame().SurfaceGetType(command.args.x, command.args.z, surfaceType);
		vector normal = GetGame().SurfaceGetNormal(command.args.x, command.args.z);

		if (!IsFiniteFloat(surfaceY) || !IsFiniteFloat(normal[0]) || !IsFiniteFloat(normal[1]) || !IsFiniteFloat(normal[2]))
		{
			result.ok = false;
			result.error = "surface_unresolved";
			return true;
		}

		result.y = surfaceY;
		result.type = surfaceType;
		result.normal = new array<float>();
		VectorToArray(normal, result.normal);
		result.ok = true;
		return true;
	}

	// Teleport a connected player. y==0 snaps to SurfaceY (scriptconsolegeneraltab.c:246-251).
	// Empty uid targets the first human; a set uid resolves by PlayerIdentity.GetPlainId().
	// Occupant in a vehicle moves the transport (GetTransform / SetTransform).
	protected bool DispatchPlayerTeleport(MCPCommand command, MCPResult result)
	{
		MCPSpawnValidation validation = ValidatePositionArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		string resolveError = "";
		PlayerBase player = ResolvePlayer(command.args, resolveError);
		if (!player)
		{
			result.ok = false;
			result.error = resolveError;
			return true;
		}

		vector position = validation.pos;
		if (position[1] == 0)
		{
			position[1] = GetGame().SurfaceY(position[0], position[2]);
		}

		Transport veh = null;
		HumanCommandVehicle vehicleCommand = player.GetCommand_Vehicle();
		if (vehicleCommand)
		{
			veh = vehicleCommand.GetTransport();
		}
		if (!veh)
		{
			veh = Transport.Cast(player.GetParent());
		}

		vector applied;
		if (veh)
		{
			CarScript car = CarScript.Cast(veh);
			// Client-owned seat (vehicle_get_in_client): SetTransform desyncs
			// the owning client (fb-20260915-014733-81f3).
			if (!car || !car.IsAuthorityOwner())
			{
				result.ok = false;
				result.error = "occupant_client_seated";
				return true;
			}
			vector mat[4];
			veh.GetTransform(mat);
			mat[3] = position;
			veh.SetTransform(mat);
			// The occupant's own position lags the transport until the next sim frame;
			// the transport is what moved, so it is what pos_real reports.
			applied = veh.GetPosition();
		}
		else if (player.IsInTransport())
		{
			result.ok = false;
			result.error = "occupant_client_seated";
			return true;
		}
		else
		{
			player.SetPosition(position);
			applied = player.GetPosition();
		}

		result.pos_real = new array<float>();
		VectorToArray(applied, result.pos_real);
		result.ok = true;
		return true;
	}

	// player_heal (inbox bef7): the vanilla full heal (PlayerBase.MCPHealServer,
	// MCP_PlayerCare.c) on the server's PlayerBase, seated or not. Empty uid targets
	// the first human; a set uid resolves by PlayerIdentity.GetPlainId(). Refusals
	// come before anything changes, and a dead body is refused: the heal would not
	// revive it. Nothing moves the player or touches its seat; the vitals are read
	// before and after. A worn splint comes off as vanilla takes it off when legs
	// heal, and the reply says whether and where the Splint item went.
	protected bool DispatchPlayerHeal(MCPCommand command, MCPResult result)
	{
		string healResolveError;
		PlayerBase healPlayer;
		MCPPlayerHeal healReport;
		string healSplintTo;
		healResolveError = "";
		healSplintTo = "";
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		healPlayer = ResolvePlayer(command.args, healResolveError);
		if (!healPlayer)
		{
			result.ok = false;
			result.error = healResolveError;
			return true;
		}

		if (!healPlayer.IsAlive())
		{
			result.ok = false;
			result.error = "player_dead";
			return true;
		}

		healReport = new MCPPlayerHeal();
		healReport.full = command.args.full;
		healReport.in_vehicle = healPlayer.IsInVehicle();
		FillPlayerVitals(healPlayer, healReport.before);
		healSplintTo = healPlayer.MCPHealServer(command.args.full);
		FillPlayerVitals(healPlayer, healReport.after);
		healReport.splint_returned = healSplintTo == "inventory" || healSplintTo == "ground";
		healReport.splint_returned_to = healSplintTo;
		result.player_heal = healReport;
		result.ok = true;
		return true;
	}

	// player_godmode (inbox 3136): the identity's choice for the rest of the mission,
	// applied to the server's PlayerBase only, never to its vehicle
	// (MCPGodmode.Choose, MCP_PlayerCare.c). Empty uid targets the first human; a set
	// uid resolves by PlayerIdentity.GetPlainId(). The choice is kept by identity, so
	// a body without one is refused, as is a dead body. The reply is the state before
	// and after, from GetAllowDamage.
	protected bool DispatchPlayerGodmode(MCPCommand command, MCPResult result)
	{
		string godmodeResolveError;
		PlayerBase godmodePlayer;
		PlayerIdentity godmodeIdentity;
		MCPPlayerGodmode godmodeReport;
		godmodeResolveError = "";
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		godmodePlayer = ResolvePlayer(command.args, godmodeResolveError);
		if (!godmodePlayer)
		{
			result.ok = false;
			result.error = godmodeResolveError;
			return true;
		}

		if (!godmodePlayer.IsAlive())
		{
			result.ok = false;
			result.error = "player_dead";
			return true;
		}

		godmodeIdentity = godmodePlayer.GetIdentity();
		if (!godmodeIdentity)
		{
			result.ok = false;
			result.error = "no_identity";
			return true;
		}

		godmodeReport = new MCPPlayerGodmode();
		godmodeReport.before = MCPGodmode.IsOn(godmodePlayer);
		MCPGodmode.Choose(godmodePlayer, godmodeIdentity.GetPlainId(), command.args.godmode);
		godmodeReport.after = MCPGodmode.IsOn(godmodePlayer);
		result.player_godmode = godmodeReport;
		result.ok = true;
		return true;
	}

	// player_kill (inbox f4de). uid is required: an empty uid must not select the
	// first human. Death is SetHealth(0), the server primitive EmoteManager.KillPlayer
	// uses (emotemanager.c). EEKilled is not called. Body godmode is released for
	// the hit only; the identity's remembered choice is not written. No respawn.
	protected bool DispatchPlayerKill(MCPCommand command, MCPResult result)
	{
		string killUid;
		Human killHuman;
		PlayerBase killPlayer;
		PlayerIdentity killIdentity;
		bool damageAllowed;
		MCPPlayerKill killReport;
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		killUid = command.args.uid;
		if (killUid == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		killHuman = FindHumanByUid(killUid);
		if (!killHuman)
		{
			result.ok = false;
			result.error = "player_not_found";
			return true;
		}
		killPlayer = PlayerBase.Cast(killHuman);
		if (!killPlayer)
		{
			result.ok = false;
			result.error = "player_not_found";
			return true;
		}
		killIdentity = killPlayer.GetIdentity();
		if (!killIdentity)
		{
			result.ok = false;
			result.error = "no_identity";
			return true;
		}
		if (killIdentity.GetPlainId() != killUid)
		{
			result.ok = false;
			result.error = "player_not_found";
			return true;
		}
		if (!killPlayer.IsAlive())
		{
			result.ok = false;
			result.error = "player_dead";
			return true;
		}

		damageAllowed = killPlayer.GetAllowDamage();
		killReport = new MCPPlayerKill();
		killReport.uid = killUid;
		killReport.health_before = killPlayer.GetHealth("", "");
		killReport.alive_before = killPlayer.IsAlive();
		killReport.godmode_policy_preserved = true;
		MCPGodmode.ReleaseBody(killPlayer, "player_kill");
		killPlayer.SetHealth(0);
		killReport.health_after = killPlayer.GetHealth("", "");
		killReport.alive_after = killPlayer.IsAlive();
		killReport.killed = !killReport.alive_after && killReport.health_after <= 0.0;
		result.player_kill = killReport;
		if (!killReport.killed)
		{
			killPlayer.SetAllowDamage(damageAllowed);
			result.ok = false;
			result.error = "kill_not_applied";
			return true;
		}
		result.ok = true;
		return true;
	}

	// bot_start (inbox 120f). Wire shape is checked before any dummy init.
	// On a build without the 1.30 bot macros, Available() is false and the
	// answer is bot_unavailable with no mutation.
	protected bool DispatchBotStart(MCPCommand command, MCPResult result)
	{
		string botError;
		bool botStarted;
		MCPBotReport botReport;
		if (!BotStartArgsOk(command))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (!MCPBotControl.Available())
		{
			result.ok = false;
			result.error = "bot_unavailable";
			return true;
		}
		botStarted = false;
		botError = MCPBotControl.Start(this, command.args.object_id, command.args.action, command.args.bot_ttl_s, m_ElapsedS, command.id, botStarted);
		if (botError != "")
		{
			result.ok = false;
			result.error = botError;
			return true;
		}
		botReport = new MCPBotReport();
		botReport.object_id = command.args.object_id;
		botReport.action = command.args.action;
		botReport.started = botStarted;
		botReport.ttl_s = command.args.bot_ttl_s;
		result.bot = botReport;
		result.ok = true;
		return true;
	}

	protected bool DispatchBotStop(MCPCommand command, MCPResult result)
	{
		string botError;
		bool botStopped;
		string botReleasedBy;
		MCPBotReport botReport;
		if (!command.args || command.args.object_id <= 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (!MCPBotControl.Available())
		{
			result.ok = false;
			result.error = "bot_unavailable";
			return true;
		}
		botStopped = false;
		botReleasedBy = "";
		botError = MCPBotControl.Stop(this, command.args.object_id, botStopped, botReleasedBy);
		if (botError != "")
		{
			result.ok = false;
			result.error = botError;
			return true;
		}
		botReport = new MCPBotReport();
		botReport.object_id = command.args.object_id;
		botReport.stopped = botStopped;
		botReport.released_by = botReleasedBy;
		result.bot = botReport;
		result.ok = true;
		return true;
	}

	protected bool BotActionAllowed(string action)
	{
		if (action == "PLAYER_BOT_RANDOMIZE_STANCE")
		{
			return true;
		}
		if (action == "PLAYER_BOT_RANDOMIZE_MOVEMENT")
		{
			return true;
		}
		if (action == "PLAYER_BOT_SPAM_USER_ACTIONS")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_ATTACH_AND_DROP_CYCLE")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_ITEM_MOVE_BACK_AND_FORTH")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_SPAWN_OPEN")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_SPAWN_OPEN_DESTROY")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_SPAWN_OPEN_EAT")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_SWAP_G2H")
		{
			return true;
		}
		if (action == "PLAYER_BOT_TEST_SWAP_INTERNAL")
		{
			return true;
		}
		return false;
	}

	protected bool BotStartArgsOk(MCPCommand command)
	{
		float ttl;
		if (!command.args || command.args.object_id <= 0)
		{
			return false;
		}
		if (!BotActionAllowed(command.args.action))
		{
			return false;
		}
		ttl = command.args.bot_ttl_s;
		if (!IsFiniteFloat(ttl))
		{
			return false;
		}
		if (ttl <= 0.0 || ttl > 30.0)
		{
			return false;
		}
		return true;
	}

	Object RuntimeObjectById(int objectId)
	{
		if (objectId <= 0 || !m_RuntimeObjects || !m_RuntimeObjects.Contains(objectId))
		{
			return null;
		}
		return m_RuntimeObjects.Get(objectId);
	}

	// player_heal vitals of one server PlayerBase (MCPPlayerVitals, MCPMessages.c).
	// A stat that does not exist leaves its value at 0 (playerbase.c:7843-7868).
	protected void FillPlayerVitals(PlayerBase target, MCPPlayerVitals vitals)
	{
		PlayerStat<float> vitalsWater;
		PlayerStat<float> vitalsEnergy;
		vitals.health = target.GetHealth01("", "");
		vitals.blood = target.GetHealth01("", "Blood");
		vitals.shock = target.GetHealth01("", "Shock");
		vitals.broken_legs = target.GetBrokenLegs() != eBrokenLegs.NO_BROKEN_LEGS;
		vitals.unconscious = target.IsUnconscious();
		vitals.bleeding_sources = target.GetBleedingSourceCount();
		vitalsWater = target.GetStatWater();
		if (vitalsWater)
		{
			vitals.water = vitalsWater.Get();
		}
		vitalsEnergy = target.GetStatEnergy();
		if (vitalsEnergy)
		{
			vitals.energy = vitalsEnergy.Get();
		}
	}

	// F3.4: read GetAnimationPhase or write SetAnimationPhaseNow on Entity (entity.c:12-25).
	// phase_set true writes phase; false, which is what an absent key arrives as, only reads
	// (fb-20260930-065425-8779: testing phase against the float.MAX sentinel made every read
	// write 0, which ficha df3a took for a written phase that reverts). Writes use the Now
	// variant, which does not make the same-tick re-read fresh: the reply can still read the
	// old value. Measured 2026-09-30 with vehicle_door reads, a written car door phase holds.
	// Resolution: args.object_id > 0 selects from the runtime registry (position-independent,
	// reaches a client-authoritative fixture whose server replica never left spawn);
	// otherwise classname near pos within OBJECT_LOOKUP_RADIUS.
	protected bool DispatchObjectAnim(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.source == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		bool writePhase = command.args.phase_set;
		if (writePhase && !IsFiniteFloat(command.args.phase))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Object match = ResolveCommandObject(command.args, error);
		if (!match)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		Entity entity = Entity.Cast(match);
		if (!entity)
		{
			result.ok = false;
			result.error = "not_entity";
			return true;
		}

		if (writePhase)
		{
			entity.SetAnimationPhaseNow(command.args.source, command.args.phase);
		}

		result.phase = entity.GetAnimationPhase(command.args.source);
		result.source = command.args.source;
		result.type = match.GetType();
		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}
		result.ok = true;
		return true;
	}

	// vehicle_door: the server half of ActionCarDoorsOutside (actioncardoorsoutside.c:66-118)
	// on one door of a CarScript, found through the attached CarDoor parts
	// (MCPCarDoorResolver). mode is explicit: read writes nothing; open and close call
	// ForceUpdateLightsStart, SetAnimationPhase on the target (not SetAnimationPhaseNow)
	// and ForceUpdateLightsEnd VEHICLE_DOOR_LIGHTS_PULSE_MS later. phase and state are
	// read before any write; phase_reply and state_reply in the same tick after it.
	// Neither reach nor IsAreaAtDoorFree is checked, and a client-owned car is not
	// refused: is_authority_owner reports it. Target resolution is ResolveCommandObject.
	protected bool DispatchVehicleDoor(MCPCommand command, MCPResult result)
	{
		string error = "";
		string doorMode = "";
		string crewSlot = "";
		string doorSelection = "";
		int doorComponent = -1;
		float targetPhase = 0.0;
		vector doorWorldPos = vector.Zero;
		Object match = null;
		CarScript car = null;
		CarDoor carDoor = null;
		MCPVehicleDoor report = null;

		if (!command.args || command.args.source == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		// An absent mode arrives as "" and is refused: it never means a write.
		doorMode = command.args.mode;
		if (doorMode != "read" && doorMode != "open" && doorMode != "close")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		match = ResolveCommandObject(command.args, error);
		if (!match)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		result.type = match.GetType();
		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}

		car = CarScript.Cast(match);
		if (!car)
		{
			result.ok = false;
			result.error = "not_a_car";
			return true;
		}

		report = new MCPVehicleDoor();
		report.source = command.args.source;
		report.mode = doorMode;
		report.is_authority_owner = car.IsAuthorityOwner();
		report.tick_time_s = GetGame().GetTickTime();
		report.phase = car.GetAnimationPhase(command.args.source);
		result.vehicle_door = report;

		// An empty crew-door slot leaves the outside action nothing to target
		// (actioncardoorsoutside.c:34-35). The refusal still carries slot and phase.
		crewSlot = MCPCarDoorResolver.CrewDoorSlot(car, command.args.source);
		if (crewSlot != "" && !car.FindAttachmentBySlotName(crewSlot))
		{
			report.slot = crewSlot;
			report.state = "missing";
			result.ok = false;
			result.error = "door_missing";
			return true;
		}

		if (!MCPCarDoorResolver.Resolve(car, command.args.source, carDoor, doorComponent, doorSelection))
		{
			result.ok = false;
			result.error = "door_not_found";
			return true;
		}

		report.door_type = carDoor.GetType();
		report.slot = MCPCarDoorResolver.SlotName(carDoor);
		report.selection = doorSelection;
		report.component_index = doorComponent;
		report.state = MCPCarDoorResolver.StateName(car.GetCarDoorsState(report.slot));
		doorWorldPos = carDoor.ModelToWorld(carDoor.GetSelectionPositionMS(doorSelection));
		VectorToArray(doorWorldPos, report.pos);

		if (doorMode != "read")
		{
			if (doorMode == "open")
			{
				targetPhase = 1.0;
			}
			car.ForceUpdateLightsStart();
			car.SetAnimationPhase(command.args.source, targetPhase);
			GetGame().GetCallQueue(CALL_CATEGORY_GAMEPLAY).CallLater(car.ForceUpdateLightsEnd, VEHICLE_DOOR_LIGHTS_PULSE_MS, false);
			report.written = true;
			report.phase_requested = targetPhase;
		}

		// Same tick as the write: the door takes animPeriod (0.5 s) to move, so this
		// is not a confirmation. A later mode=read is.
		report.phase_reply = car.GetAnimationPhase(command.args.source);
		report.state_reply = MCPCarDoorResolver.StateName(car.GetCarDoorsState(report.slot));
		result.ok = true;
		return true;
	}

	// Impose heading/speed on an infected server-side through its AI input controller.
	// Vanilla precedent: PluginDayZInfectedDebug drives a possessed infected the same
	// way (plugindayzinfecteddebug.c:385); combo order follows dayzanimal.c:548-550.
	// Heading arrives in degrees and is converted here (OverrideHeading takes radians).
	// mode "release" hands control back to the vanilla AI.
	protected bool DispatchInfectedDrive(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.type == "" || !command.args.pos || command.args.pos.Count() != 3)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		float px = command.args.pos.Get(0);
		float py = command.args.pos.Get(1);
		float pz = command.args.pos.Get(2);
		if (!IsFiniteFloat(px) || !IsFiniteFloat(py) || !IsFiniteFloat(pz))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		bool release = command.args.mode == "release";
		bool headingUnset = !command.args.heading_set;
		bool speedUnset = !command.args.speed_set;
		if (!release && (headingUnset || speedUnset))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (!release && (!IsFiniteFloat(command.args.heading) || !IsFiniteFloat(command.args.speed)))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Object match = FindUniqueObjectNearType(command.args.type, Vector(px, py, pz), OBJECT_LOOKUP_RADIUS, error);
		if (!match)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		DayZInfected infected = DayZInfected.Cast(match);
		if (!infected)
		{
			result.ok = false;
			result.error = "not_infected";
			return true;
		}

		DayZInfectedInputController controller = infected.GetInputController();
		if (!controller)
		{
			result.ok = false;
			result.error = "no_input_controller";
			return true;
		}

		if (release)
		{
			controller.OverrideMovementSpeed(false, 0.0);
			controller.OverrideHeading(false, 0.0);
			controller.OverrideTurnSpeed(false, 0.0);
		}
		else
		{
			controller.OverrideTurnSpeed(true, Math.PI2);
			controller.OverrideMovementSpeed(true, command.args.speed);
			controller.OverrideHeading(true, command.args.heading * Math.DEG2RAD);
		}

		vector current = infected.GetPosition();
		result.pos_real = new array<float>();
		result.pos_real.Insert(current[0]);
		result.pos_real.Insert(current[1]);
		result.pos_real.Insert(current[2]);
		result.type = command.args.type;
		result.found = true;
		result.ok = true;
		return true;
	}

	// Spawn classname into hands or inventory. Empty uid targets the first human.
	protected bool DispatchInventoryGive(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.classname == "" || (command.args.dest != "hands" && command.args.dest != "inventory"))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string resolveError = "";
		PlayerBase player = ResolvePlayer(command.args, resolveError);
		if (!player)
		{
			result.ok = false;
			result.error = resolveError;
			return true;
		}

		EntityAI spawned = null;
		if (command.args.dest == "hands")
		{
			if (player.GetItemInHands())
			{
				result.ok = false;
				result.error = "hands_occupied";
				return true;
			}

			spawned = player.GetHumanInventory().CreateInHands(command.args.classname);
		}
		else
		{
			spawned = player.GetInventory().CreateInInventory(command.args.classname);
		}

		if (!spawned)
		{
			result.ok = false;
			result.error = "create_failed";
			return true;
		}

		// Same map and key as world_spawn (command id). This id is the created
		// item for this run only, for both dests. object_delete of it removes
		// the item. A retry is a second create.
		m_RuntimeObjects.Insert(command.id, spawned);
		result.object_id = command.id;
		result.classname = command.args.classname;
		result.type = spawned.GetType();
		// 71c9: read back from the created item, for both dests. A bow (Archery_Base)
		// keeps Weapon_Base's empty InitStateMachine (weapon_base.c:133) and has no
		// state machine. Vanilla logs [wpnfsm] errors when the engine saves and loads
		// its state anyway (weapon_base.c:568, :599); the item still arrives.
		Weapon_Base spawnedWeapon = Weapon_Base.Cast(spawned);
		result.weapon_without_fsm = spawnedWeapon && !spawnedWeapon.CanProcessWeaponEvents();
		result.found = true;
		result.deferred = false;
		result.ok = true;
		return true;
	}

	// Create an item directly in one world entity's attachment slot or cargo.
	// Resolution is shared with object_inspect, so object_id is position-independent
	// and type+pos remains unique-or-fail. Success includes an immediate inventory
	// snapshot of this same target; callers can re-read it with object_inspect.
	protected bool DispatchInventoryAttach(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.classname == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (command.args.dest == "attachment")
		{
			if (command.args.slot == "")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		else if (command.args.dest == "cargo")
		{
			if (command.args.slot != "")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		else
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string attachResolveError = "";
		Object attachTarget = ResolveCommandObject(command.args, attachResolveError);
		if (!attachTarget)
		{
			result.ok = false;
			result.error = attachResolveError;
			return true;
		}

		EntityAI attachOwner = EntityAI.Cast(attachTarget);
		if (!attachOwner)
		{
			result.ok = false;
			result.error = "not_entity_ai";
			return true;
		}

		GameInventory attachInventory = attachOwner.GetInventory();
		if (!attachInventory)
		{
			result.ok = false;
			result.error = "inventory_unavailable";
			return true;
		}

		if (!IsKnownInventoryClassname(command.args.classname))
		{
			result.ok = false;
			result.error = "invalid_classname";
			return true;
		}

		EntityAI attachedItem = null;
		int attachSlotId = InventorySlots.INVALID;
		if (command.args.dest == "attachment")
		{
			attachSlotId = InventorySlots.GetSlotIdFromString(command.args.slot);
			if (!InventorySlots.IsSlotIdValid(attachSlotId))
			{
				result.ok = false;
				result.error = "slot_not_found";
				return true;
			}
			if (!attachInventory.HasAttachmentSlot(attachSlotId))
			{
				result.ok = false;
				result.error = "slot_not_found";
				return true;
			}
			if (attachInventory.FindAttachment(attachSlotId))
			{
				result.ok = false;
				result.error = "slot_occupied";
				return true;
			}

			attachedItem = attachInventory.CreateAttachmentEx(command.args.classname, attachSlotId);
			if (!attachedItem)
			{
				result.ok = false;
				result.error = "attachment_create_failed";
				return true;
			}
			if (attachInventory.FindAttachment(attachSlotId) != attachedItem)
			{
				result.ok = false;
				result.error = "attachment_postcondition_failed";
				return true;
			}
		}
		else
		{
			if (!attachInventory.GetCargo())
			{
				result.ok = false;
				result.error = "cargo_unavailable";
				return true;
			}

			attachedItem = attachInventory.CreateEntityInCargo(command.args.classname);
			if (!attachedItem)
			{
				result.ok = false;
				result.error = "cargo_create_failed";
				return true;
			}
			if (!attachInventory.HasEntityInCargo(attachedItem))
			{
				result.ok = false;
				result.error = "cargo_postcondition_failed";
				return true;
			}
		}

		// Same map and key as world_spawn (command id). This id is the created
		// item for this run only; a later daemon restarts ids at 1. result.object_id
		// below stays the destination owner. object_delete of item_object_id
		// removes the worn or cargo item and leaves the owner registered.
		m_RuntimeObjects.Insert(command.id, attachedItem);

		result.classname = command.args.classname;
		result.type = attachedItem.GetType();
		result.found = true;
		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}

		MCPInventoryAttachReceipt attachReceipt = new MCPInventoryAttachReceipt();
		attachReceipt.dest = command.args.dest;
		attachReceipt.slot = command.args.slot;
		attachReceipt.item_object_id = command.id;
		result.inventory_attach = attachReceipt;

		MCPTelemetry attachTelemetry = new MCPTelemetry();
		attachTelemetry.mode = "inventory_attach";
		PopulateTelemetryObject(attachOwner, attachTelemetry);
		result.telemetry = attachTelemetry;
		result.ok = true;
		return true;
	}

	protected bool IsKnownInventoryClassname(string classname)
	{
		if (classname == "")
		{
			return false;
		}
		if (GetGame().ConfigIsExisting("CfgVehicles " + classname))
		{
			return true;
		}
		if (GetGame().ConfigIsExisting("CfgWeapons " + classname))
		{
			return true;
		}
		if (GetGame().ConfigIsExisting("CfgMagazines " + classname))
		{
			return true;
		}
		return false;
	}

	// loadout_get (fb99): what the target wears and holds, as MCPLoadout.items
	// rows (MCPMessages.c). It writes nothing. A refusal carries no read, and a
	// read past LOADOUT_MAX_ITEMS rows or LOADOUT_MAX_DEPTH levels is
	// loadout_too_large with no read either. result.type is the target's class;
	// result.object_id echoes the request when it named one.
	protected bool DispatchLoadoutGet(MCPCommand command, MCPResult result)
	{
		string targetError = "";
		PlayerBase target = null;
		MCPLoadout loadoutRead = null;

		target = ResolveLoadoutTarget(command.args, targetError);
		if (!target)
		{
			result.ok = false;
			result.error = targetError;
			return true;
		}

		loadoutRead = new MCPLoadout();
		ReadLoadout(target, loadoutRead);
		if (loadoutRead.too_large)
		{
			result.ok = false;
			result.error = "loadout_too_large";
			return true;
		}

		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}
		result.type = target.GetType();
		result.loadout = loadoutRead;
		result.ok = true;
		return true;
	}

	// loadout_apply (220f). The rows and the target are checked before anything
	// changes; then the rows are created and the target read back, all in this
	// tick (ApplyLoadout). A loadout_clear job still pending on the target is
	// busy: what it deleted may still hold its slots.
	protected bool DispatchLoadoutApply(MCPCommand command, MCPResult result)
	{
		string rowsError = "";
		string targetError = "";
		PlayerBase target = null;

		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		rowsError = LoadoutRowsError(command.args.loadout_items);
		if (rowsError != "")
		{
			result.ok = false;
			result.error = rowsError;
			return true;
		}

		target = ResolveLoadoutTarget(command.args, targetError);
		if (!target)
		{
			result.ok = false;
			result.error = targetError;
			return true;
		}
		if (HasActiveJobFor(null, target))
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		ApplyLoadout(target, command.args, result);
		return true;
	}

	// loadout_clear (220f): what the loadout_apply tool sends first for
	// clear_first. In this tick it deletes what the target wears and holds
	// (ClearLoadout). With nothing to delete it answers now: deleted 0,
	// cleanup_complete true. Otherwise a "loadout_clear" job only watches the
	// deleted objects, changing nothing: the runner checks them each tick
	// until all read null or the deadline has passed, then the job posts
	// deleted (how many) and cleanup_complete (true when all read null at that
	// last check, which can come one tick after the deadline). Object.Delete
	// defers ObjectDelete to the next frame (object.c:82-85), and whether
	// ObjectDelete frees a slot within its own frame is not measured. uid is
	// the PlainId of the player the clear acted on, by uid or as the first
	// human, and "" for a survivor by object_id: the tool sends the creates to
	// that uid, so whoever is first human by then is never dressed in that
	// player's place (#261 review Y11). A first human with no identity is
	// player_not_found, and a target with a job pending is busy, both before
	// anything is deleted.
	protected bool DispatchLoadoutClear(MCPCommand command, MCPResult result)
	{
		string targetError = "";
		string clearedUid = "";
		PlayerBase target = null;
		PlayerIdentity identity = null;
		array<EntityAI> cleared = null;
		MCPJob job = null;

		target = ResolveLoadoutTarget(command.args, targetError);
		if (!target)
		{
			result.ok = false;
			result.error = targetError;
			return true;
		}
		if (command.args.object_id == 0)
		{
			identity = target.GetIdentity();
			if (!identity)
			{
				result.ok = false;
				result.error = "player_not_found";
				return true;
			}
			clearedUid = identity.GetPlainId();
		}
		if (HasActiveJobFor(null, target))
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		cleared = new array<EntityAI>();
		ClearLoadout(target, cleared);
		if (cleared.Count() == 0)
		{
			result.ok = true;
			result.deleted = 0;
			result.cleanup_complete = true;
			result.uid = clearedUid;
			return true;
		}

		job = new MCPJob();
		job.id = command.id;
		job.kind = "loadout_clear";
		job.args = command.args;
		job.subject = target;
		job.loadout_cleared = cleared;
		job.loadout_uid = clearedUid;
		job.deadline_s = m_ElapsedS + JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);
		Log("job queued id=" + job.id + " kind=loadout_clear deleted=" + cleared.Count() + " deadline_s=" + job.deadline_s);
		return false;
	}

	// The target of loadout_get, loadout_apply and loadout_clear. object_id > 0
	// names a registry entry: an id nothing registered, or whose object is
	// gone, is not_found; an object that is not a PlayerBase, or is a connected
	// player (uid selects those), is bad_target. object_id 0 leaves the choice
	// to uid, as ResolvePlayer makes it ("" the first human). object_id and uid
	// together, or a negative object_id, are bad_args.
	protected PlayerBase ResolveLoadoutTarget(MCPArgs args, out string error)
	{
		Object registered = null;
		PlayerBase survivor = null;

		error = "";
		if (!args)
		{
			error = "bad_args";
			return null;
		}
		if (args.object_id < 0)
		{
			error = "bad_args";
			return null;
		}
		if (args.object_id == 0)
		{
			return ResolvePlayer(args, error);
		}
		if (args.uid != "")
		{
			error = "bad_args";
			return null;
		}

		registered = RuntimeObjectById(args.object_id);
		if (!registered)
		{
			error = "not_found";
			return null;
		}
		survivor = PlayerBase.Cast(registered);
		if (!survivor)
		{
			error = "bad_target";
			return null;
		}
		if (survivor.GetIdentity())
		{
			error = "bad_target";
			return null;
		}
		return survivor;
	}

	// The rows the tool sends (tools/dayz_mcp/loadout.py flatten_request): at most
	// LOADOUT_MAX_ITEMS; each parent an earlier row or -1 (the target); dest
	// attachment with a slot, cargo inside an item, or hands once on the target,
	// both without a slot; no row more than LOADOUT_MAX_DEPTH levels down.
	// "" when they hold, else bad_args. Nothing is created before this passes.
	protected string LoadoutRowsError(array<ref MCPLoadoutItem> rows)
	{
		array<int> levels = new array<int>();
		MCPLoadoutItem row = null;
		int level = 0;
		int handsRows = 0;
		int i = 0;

		if (!rows)
		{
			return "bad_args";
		}
		if (rows.Count() > LOADOUT_MAX_ITEMS)
		{
			return "bad_args";
		}
		while (i < rows.Count())
		{
			row = rows.Get(i);
			if (!row)
			{
				return "bad_args";
			}
			if (row.type == "")
			{
				return "bad_args";
			}
			if (row.parent < -1 || row.parent >= i)
			{
				return "bad_args";
			}
			if (row.dest == "attachment")
			{
				if (row.slot == "")
				{
					return "bad_args";
				}
			}
			else if (row.dest == "cargo")
			{
				if (row.parent == -1 || row.slot != "")
				{
					return "bad_args";
				}
			}
			else if (row.dest == "hands")
			{
				if (row.parent != -1 || row.slot != "")
				{
					return "bad_args";
				}
				handsRows = handsRows + 1;
				if (handsRows > 1)
				{
					return "bad_args";
				}
			}
			else
			{
				return "bad_args";
			}

			level = 1;
			if (row.parent >= 0)
			{
				level = levels.Get(row.parent) + 1;
			}
			if (level > LOADOUT_MAX_DEPTH)
			{
				return "bad_args";
			}
			levels.Insert(level);
			i = i + 1;
		}
		return "";
	}

	// loadout_clear: deletes the target's attachments and the item in its hands
	// with GetGame().ObjectDelete, as object_delete does; what they hold goes
	// with them. Each deleted object is added to cleared first, so the walk
	// never reads a list it is changing; the engine nulls those references
	// once an object is gone (the stale read hands_take makes).
	protected void ClearLoadout(PlayerBase target, array<EntityAI> cleared)
	{
		GameInventory inventory = target.GetInventory();
		EntityAI hands = target.GetItemInHands();
		EntityAI attachment = null;
		int count = 0;
		int i = 0;

		if (inventory)
		{
			count = inventory.AttachmentCount();
			while (i < count)
			{
				attachment = inventory.GetAttachmentFromIndex(i);
				if (attachment && attachment != hands)
				{
					cleared.Insert(attachment);
				}
				i = i + 1;
			}
		}
		if (hands)
		{
			cleared.Insert(hands);
		}

		i = 0;
		while (i < cleared.Count())
		{
			if (cleared.Get(i))
			{
				GetGame().ObjectDelete(cleared.Get(i));
			}
			i = i + 1;
		}
	}

	// Creates args.loadout_items in order, then reads the target back.
	// result.loadout.applied holds one outcome per row; a row whose parent row
	// failed is parent_failed and not created. result.loadout.items is the read
	// after (too_large past the caps). result.ok is true whatever the rows did:
	// loadout_apply's status comes from the outcomes. Created items are not
	// registered in m_RuntimeObjects.
	protected void ApplyLoadout(PlayerBase target, MCPArgs args, MCPResult result)
	{
		array<EntityAI> created = new array<EntityAI>();
		MCPLoadout loadoutReply = new MCPLoadout();
		MCPLoadoutApplied outcome = null;
		MCPLoadoutItem row = null;
		EntityAI owner = null;
		EntityAI item = null;
		string itemError = "";
		int i = 0;

		while (i < args.loadout_items.Count())
		{
			row = args.loadout_items.Get(i);
			item = null;
			itemError = "";
			owner = target;
			if (row.parent >= 0)
			{
				owner = created.Get(row.parent);
			}
			if (!owner)
			{
				itemError = "parent_failed";
			}
			else
			{
				item = CreateLoadoutItem(target, owner, row, itemError);
			}
			created.Insert(item);

			outcome = new MCPLoadoutApplied();
			if (item)
			{
				outcome.ok = true;
			}
			else
			{
				outcome.ok = false;
				outcome.error = itemError;
			}
			loadoutReply.applied.Insert(outcome);
			i = i + 1;
		}

		ReadLoadout(target, loadoutReply);
		if (args.object_id > 0)
		{
			result.object_id = args.object_id;
		}
		result.type = target.GetType();
		result.loadout = loadoutReply;
		result.ok = true;
	}

	// One row into owner: an attachment in its slot (the checks and the
	// postcondition inventory_attach makes), an item in owner's cargo, or the
	// target's hands (CreateInHands, as inventory_give). An item that is created
	// but is not where it was asked for is deleted, so a failed row leaves
	// nothing behind. error is unknown_type, slot_not_found, slot_occupied,
	// cannot_attach or no_room; an occupied slot or hands keep what is there.
	protected EntityAI CreateLoadoutItem(PlayerBase target, EntityAI owner, MCPLoadoutItem row, out string error)
	{
		GameInventory inventory = owner.GetInventory();
		EntityAI item = null;
		int slotId = InventorySlots.INVALID;

		error = "";
		if (!IsKnownInventoryClassname(row.type))
		{
			error = "unknown_type";
			return null;
		}

		if (row.dest == "hands")
		{
			if (target.GetItemInHands())
			{
				error = "slot_occupied";
				return null;
			}
			item = target.GetHumanInventory().CreateInHands(row.type);
			if (!item)
			{
				error = "cannot_attach";
				return null;
			}
			if (target.GetItemInHands() != item)
			{
				GetGame().ObjectDelete(item);
				error = "cannot_attach";
				return null;
			}
			return item;
		}

		if (row.dest == "attachment")
		{
			if (!inventory)
			{
				error = "slot_not_found";
				return null;
			}
			slotId = InventorySlots.GetSlotIdFromString(row.slot);
			if (!InventorySlots.IsSlotIdValid(slotId))
			{
				error = "slot_not_found";
				return null;
			}
			if (!inventory.HasAttachmentSlot(slotId))
			{
				error = "slot_not_found";
				return null;
			}
			if (inventory.FindAttachment(slotId))
			{
				error = "slot_occupied";
				return null;
			}
			item = inventory.CreateAttachmentEx(row.type, slotId);
			if (!item)
			{
				error = "cannot_attach";
				return null;
			}
			if (inventory.FindAttachment(slotId) != item)
			{
				GetGame().ObjectDelete(item);
				error = "cannot_attach";
				return null;
			}
			return item;
		}

		if (!inventory)
		{
			error = "no_room";
			return null;
		}
		if (!inventory.GetCargo())
		{
			error = "no_room";
			return null;
		}
		item = inventory.CreateEntityInCargo(row.type);
		if (!item)
		{
			error = "no_room";
			return null;
		}
		if (!inventory.HasEntityInCargo(item))
		{
			GetGame().ObjectDelete(item);
			error = "no_room";
			return null;
		}
		return item;
	}

	// The target's attachments, in GetAttachmentFromIndex order, then the item
	// in its hands, each followed by what it holds, as rows of loadoutRead.items.
	// The hands item is skipped among the attachments in case the engine lists
	// it there too. Past the caps too_large is set and items emptied.
	protected void ReadLoadout(PlayerBase target, MCPLoadout loadoutRead)
	{
		GameInventory inventory = target.GetInventory();
		EntityAI hands = target.GetItemInHands();
		EntityAI attachment = null;
		int count = 0;
		int i = 0;

		if (inventory)
		{
			count = inventory.AttachmentCount();
			while (i < count)
			{
				attachment = inventory.GetAttachmentFromIndex(i);
				if (attachment && attachment != hands)
				{
					AppendLoadoutRow(loadoutRead, attachment, "attachment", -1, 1);
				}
				i = i + 1;
			}
		}
		if (hands)
		{
			AppendLoadoutRow(loadoutRead, hands, "hands", -1, 1);
		}
		if (loadoutRead.too_large)
		{
			loadoutRead.items.Clear();
		}
	}

	// One row for item, then its attachments and its cargo, depth first. level
	// is 1 for what the target wears or holds; parent is the row item sits in.
	protected void AppendLoadoutRow(MCPLoadout loadoutRead, EntityAI item, string dest, int parent, int level)
	{
		MCPLoadoutItem row = null;
		GameInventory inventory = null;
		CargoBase cargo = null;
		EntityAI child = null;
		int rowIndex = 0;
		int count = 0;
		int i = 0;

		if (loadoutRead.too_large)
		{
			return;
		}
		if (level > LOADOUT_MAX_DEPTH || loadoutRead.items.Count() >= LOADOUT_MAX_ITEMS)
		{
			loadoutRead.too_large = true;
			return;
		}

		row = new MCPLoadoutItem();
		row.type = item.GetType();
		row.dest = dest;
		row.parent = parent;
		if (dest == "attachment")
		{
			row.slot = LoadoutSlotName(item);
		}
		rowIndex = loadoutRead.items.Count();
		loadoutRead.items.Insert(row);

		inventory = item.GetInventory();
		if (!inventory)
		{
			return;
		}
		count = inventory.AttachmentCount();
		while (i < count)
		{
			child = inventory.GetAttachmentFromIndex(i);
			if (child)
			{
				AppendLoadoutRow(loadoutRead, child, "attachment", rowIndex, level + 1);
			}
			i = i + 1;
		}

		cargo = inventory.GetCargo();
		if (!cargo)
		{
			return;
		}
		count = cargo.GetItemCount();
		i = 0;
		while (i < count)
		{
			child = cargo.GetItem(i);
			if (child)
			{
				AppendLoadoutRow(loadoutRead, child, "cargo", rowIndex, level + 1);
			}
			i = i + 1;
		}
	}

	// The slot an attached item sits in, as InventorySlots.GetSlotName gives it:
	// the read MCP_CarDoor.c SlotName makes. "" when the engine reports none.
	protected string LoadoutSlotName(EntityAI item)
	{
		GameInventory itemInventory = item.GetInventory();
		InventoryLocation location = null;

		if (!itemInventory)
		{
			return "";
		}
		location = new InventoryLocation();
		if (!itemInventory.GetCurrentInventoryLocation(location))
		{
			return "";
		}
		if (location.GetSlot() == -1)
		{
			return "";
		}
		return InventorySlots.GetSlotName(location.GetSlot());
	}

	// The "loadout_clear" job is ready once no object the clear deleted is
	// still referenced. DispatchLoadoutClear queues it only with objects in
	// loadout_cleared.
	protected bool IsLoadoutClearDone(MCPJob job)
	{
		int i = 0;

		while (i < job.loadout_cleared.Count())
		{
			if (job.loadout_cleared.Get(i))
			{
				return false;
			}
			i = i + 1;
		}
		return true;
	}

	// The "loadout_clear" job's reply: what it saw, and nothing else. deleted is
	// how many objects the clear deleted; cleanup_complete is true when each
	// read null at the job's last check (PostJobSuccess), which can come one
	// tick after the deadline, and false when that check found one still there
	// past the deadline (PostJobTimeout). uid is the dispatch's, unchanged.
	protected void PostLoadoutClearJob(MCPJob job, bool complete)
	{
		MCPResult result = new MCPResult();

		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		result.ok = true;
		result.deleted = job.loadout_cleared.Count();
		result.cleanup_complete = complete;
		result.uid = job.loadout_uid;
		PostResult(result);
	}

	// Raw nearby objects via GetObjectsAtPosition3D. No classname filter.
	// result.entities is the nearest `limit` hits; result.count_total is the uncut size.
	// has_cargo reports cargo capacity (HasCargoCapacity), never occupancy.
	protected bool DispatchEntitiesQuery(MCPCommand command, MCPResult result)
	{
		MCPSpawnValidation validation = ValidatePositionArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		if (!command.args || command.args.radius <= 0.0 || !IsFiniteFloat(command.args.radius) || command.args.radius > 200.0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int limit = command.args.limit;
		if (limit <= 0 || limit > 128)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(validation.pos, command.args.radius, m_ReadyObjects, m_ReadyProxyCargos);

		array<ref MCPEntityHit> collected = new array<ref MCPEntityHit>();
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found)
			{
				MCPEntityHit entry = new MCPEntityHit();
				vector foundPos = found.GetPosition();
				entry.type = found.GetType();
				entry.classname = found.ClassName();
				entry.has_cargo = HasCargoCapacity(found);
				VectorToArray(foundPos, entry.pos);
				entry.distance = vector.Distance(validation.pos, foundPos);
				collected.Insert(entry);
			}

			i = i + 1;
		}

		result.count_total = collected.Count();
		result.entities = TakeNearestEntities(collected, limit);
		result.ok = true;
		return true;
	}

	// C3: cargo capacity, not occupancy. An entity that owns a cargo grid answers
	// true even while that grid is empty; an Object that is not an EntityAI, or an
	// EntityAI without cargo, answers false. Item counting and classname lists are
	// deliberately not consulted.
	protected bool HasCargoCapacity(Object found)
	{
		EntityAI entity = EntityAI.Cast(found);
		if (!entity)
		{
			return false;
		}

		GameInventory inventory = entity.GetInventory();
		if (!inventory)
		{
			return false;
		}

		return inventory.GetCargo() != null;
	}

	// Building door predicates (3_game/entities/building.c). Not GetAnimationPhase:
	// that read stays in DispatchObjectAnim. OpenDoor, CloseDoor, LockDoor and
	// UnlockDoor are not called. A non-Building is not_a_building.
	protected bool DispatchObjectDoors(MCPCommand command, MCPResult result)
	{
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Object match = ResolveCommandObject(command.args, error);
		if (!match)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		result.type = match.GetType();
		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}

		Building building = Building.Cast(match);
		if (!building)
		{
			result.ok = false;
			result.error = "not_a_building";
			return true;
		}

		int doorCount = building.GetDoorCount();
		MCPBuildingDoors report = new MCPBuildingDoors();
		report.door_count = doorCount;
		result.building_doors = report;
		if (doorCount < 0 || doorCount > DOOR_READ_MAX)
		{
			result.ok = false;
			result.error = "door_count_unsupported";
			return true;
		}

		int doorIndex = 0;
		while (doorIndex < doorCount)
		{
			MCPDoorState row = new MCPDoorState();
			row.index = doorIndex;
			row.open = building.IsDoorOpen(doorIndex);
			row.opening = building.IsDoorOpening(doorIndex);
			row.opening_ajar = building.IsDoorOpeningAjar(doorIndex);
			row.opened = building.IsDoorOpened(doorIndex);
			row.ajar = building.IsDoorOpenedAjar(doorIndex);
			row.closing = building.IsDoorClosing(doorIndex);
			row.closed = building.IsDoorClosed(doorIndex);
			row.locked = building.IsDoorLocked(doorIndex);
			vector doorSoundPos = building.GetDoorSoundPos(doorIndex);
			VectorToArray(doorSoundPos, row.pos);
			report.doors.Insert(row);
			doorIndex = doorIndex + 1;
		}

		result.ok = true;
		return true;
	}

	// F3.6: memory points and bounding center. Missing memory points are product FAIL
	// (exists:false) with ok:true — never a tool error.
	protected bool DispatchObjectInspect(MCPCommand command, MCPResult result)
	{
		if (!command.args || !command.args.want || command.args.want.Count() == 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Object match = ResolveCommandObject(command.args, error);
		if (!match)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		MCPObjectInspect inspect = new MCPObjectInspect();
		inspect.type = match.GetType();

		int i = 0;
		while (i < command.args.want.Count())
		{
			string wantName = command.args.want.Get(i);
			// Add a snapshot of the SAME resolved object, including by registry ID.
			// Preserve the legacy memory-point result even for a point named inventory.
			if (wantName == "inventory" && !result.telemetry)
			{
				MCPTelemetry inventoryTelemetry = new MCPTelemetry();
				inventoryTelemetry.mode = "object_inspect";
				PopulateTelemetryObject(match, inventoryTelemetry);
				result.telemetry = inventoryTelemetry;
			}
			if (wantName == "bounding_center")
			{
				vector center = match.GetBoundingCenter();
				VectorToArray(center, inspect.bounding_center);
				inspect.has_bounding_center = true;
			}
			else if (wantName == "quantity")
			{
				FillInspectQuantity(match, inspect);
			}
			else
			{
				MCPMemoryPoint point = new MCPMemoryPoint();
				point.name = wantName;
				if (match.MemoryPointExists(wantName))
				{
					point.exists = true;
					VectorToArray(match.GetMemoryPointPos(wantName), point.pos);
				}
				else
				{
					point.exists = false;
				}

				inspect.memory_points.Insert(point);
			}

			i = i + 1;
		}

		result.inspect = inspect;
		result.type = inspect.type;
		if (command.args.object_id > 0)
		{
			result.object_id = command.args.object_id;
		}
		result.ok = true;
		return true;
	}

	// object_inspect want quantity (inbox fb98): an item's quantity, read without
	// changing it. Anything but an ItemBase with a quantity leaves has_quantity false.
	protected void FillInspectQuantity(Object match, MCPObjectInspect inspect)
	{
		ItemBase item = ItemBase.Cast(match);
		if (item && item.HasQuantity())
		{
			inspect.has_quantity = true;
			inspect.quantity = item.GetQuantity();
			inspect.quantity_max = item.GetQuantityMax();
		}
	}

	// Shared resolution for anim/inspect verbs. object_id > 0 selects from the runtime
	// registry (world_spawn, inventory_attach's created item, fixture cars included)
	// and needs no position; otherwise classname near pos. object_id_unknown covers
	// never-registered and already-deleted ids alike (delete removes the registry entry).
	// An inventory_attach id is the child, not the destination owner, and lasts this run.
	protected Object ResolveCommandObject(MCPArgs args, out string error)
	{
		if (args.object_id > 0)
		{
			if (!m_RuntimeObjects || !m_RuntimeObjects.Contains(args.object_id))
			{
				error = "object_id_unknown";
				return null;
			}

			Object registered = m_RuntimeObjects.Get(args.object_id);
			if (!registered)
			{
				error = "object_id_stale";
				return null;
			}

			return registered;
		}

		if (args.type == "" || !args.pos || args.pos.Count() != 3)
		{
			error = "bad_args";
			return null;
		}

		float px = args.pos.Get(0);
		float py = args.pos.Get(1);
		float pz = args.pos.Get(2);
		if (!IsFiniteFloat(px) || !IsFiniteFloat(py) || !IsFiniteFloat(pz))
		{
			error = "bad_args";
			return null;
		}

		return FindUniqueObjectNearType(args.type, Vector(px, py, pz), OBJECT_LOOKUP_RADIUS, error);
	}

	// Register one existing object of args.type inside the caller's radius.
	// Radius is finite and in (0, OBJECT_RESOLVE_RADIUS_MAX]. Exact type, one
	// match. An object already in m_RuntimeObjects keeps that id; otherwise
	// the id is command.id. FindUniqueObjectNearType is unchanged.
	protected bool DispatchObjectResolve(MCPCommand command, MCPResult result)
	{
		string resolveError = "";
		float px;
		float py;
		float pz;
		float radius;
		Object match;
		int existingId;
		int objectId;

		if (!command.args || command.args.type == "" || !command.args.pos || command.args.pos.Count() != 3)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		px = command.args.pos.Get(0);
		py = command.args.pos.Get(1);
		pz = command.args.pos.Get(2);
		radius = command.args.radius;
		if (!IsFiniteFloat(px) || !IsFiniteFloat(py) || !IsFiniteFloat(pz) || !IsFiniteFloat(radius) || radius <= 0.0 || radius > OBJECT_RESOLVE_RADIUS_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		match = FindUniqueObjectNearType(command.args.type, Vector(px, py, pz), radius, resolveError);
		if (!match)
		{
			result.ok = false;
			result.error = resolveError;
			return true;
		}

		existingId = RuntimeObjectId(match);
		objectId = existingId;
		if (existingId <= 0)
		{
			m_RuntimeObjects.Insert(command.id, match);
			objectId = command.id;
		}

		result.object_id = objectId;
		result.type = match.GetType();
		result.pos_real = new array<float>();
		VectorToArray(match.GetPosition(), result.pos_real);
		result.ok = true;
		return true;
	}

	// Resolve a single world object by classname near pos. Zero matches -> object_not_found;
	// more than one -> ambiguous_object. Used by object_anim, object_inspect and object_doors.
	protected Object FindUniqueObjectNearType(string typeName, vector pos, float radius, out string error)
	{
		error = "object_not_found";
		if (typeName == "" || radius <= 0.0 || !IsFiniteFloat(radius))
		{
			error = "bad_args";
			return null;
		}

		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, radius, m_ReadyObjects, m_ReadyProxyCargos);

		Object match = null;
		int matchCount = 0;
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found && found.GetType() == typeName)
			{
				matchCount = matchCount + 1;
				if (matchCount == 1)
				{
					match = found;
				}
			}

			i = i + 1;
		}

		if (matchCount == 0)
		{
			error = "object_not_found";
			return null;
		}

		if (matchCount > 1)
		{
			error = "ambiguous_object";
			return null;
		}

		error = "";
		return match;
	}

	protected bool DispatchWorldTimeSet(MCPCommand command, MCPResult result)
	{
		if (!ValidateWorldTimeArgs(command.args, result))
		{
			return true;
		}

		World world = GetGame().GetWorld();
		if (!world)
		{
			result.ok = false;
			result.error = "world_unavailable";
			return true;
		}

		world.SetDate(command.args.year, command.args.month, command.args.day, command.args.hour, command.args.minute);
		if (command.args.time_multiplier_set)
		{
			world.SetTimeMultiplier(command.args.time_multiplier);
		}

		int appliedYear;
		int appliedMonth;
		int appliedDay;
		int appliedHour;
		int appliedMinute;
		world.GetDate(appliedYear, appliedMonth, appliedDay, appliedHour, appliedMinute);

		MCPApplied applied = new MCPApplied();
		applied.year = appliedYear;
		applied.month = appliedMonth;
		applied.day = appliedDay;
		applied.hour = appliedHour;
		applied.minute = appliedMinute;
		result.applied = applied;
		result.ok = true;
		return true;
	}

	// world_time_get is a read: the same World.GetDate that DispatchWorldTimeSet
	// makes after its write, and nothing is written. World has SetTimeMultiplier
	// and no getter (world.c:19), so the multiplier is not read.
	protected bool DispatchWorldTimeGet(MCPCommand command, MCPResult result)
	{
		int year;
		int month;
		int day;
		int hour;
		int minute;
		World world = GetGame().GetWorld();
		MCPWorldTime worldTime;
		if (!world)
		{
			result.ok = false;
			result.error = "world_unavailable";
			return true;
		}

		world.GetDate(year, month, day, hour, minute);
		worldTime = new MCPWorldTime();
		worldTime.year = year;
		worldTime.month = month;
		worldTime.day = day;
		worldTime.hour = hour;
		worldTime.minute = minute;
		result.world_time = worldTime;
		result.ok = true;
		return true;
	}

	// server_perf (2427): the frame rate is measured, not asked of an FPS API.
	// The job counts the server frames of the window in IsServerPerfReady, and
	// PostServerPerfSuccess counts the entities once the window has closed.
	protected bool DispatchServerPerf(MCPCommand command, MCPResult result)
	{
		MCPJob job;
		if (!command.args || !IsFiniteFloat(command.args.window_s) || command.args.window_s < SERVER_PERF_WINDOW_MIN_S || command.args.window_s > SERVER_PERF_WINDOW_MAX_S)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		job = new MCPJob();
		job.id = command.id;
		job.kind = "server_perf";
		job.args = command.args;
		job.sample_s_target = command.args.window_s;
		job.deadline_s = m_ElapsedS + command.args.window_s + JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);

		Log("job queued id=" + job.id + " kind=server_perf window_s=" + job.sample_s_target + " deadline_s=" + job.deadline_s);
		return false;
	}

	// nav_path_query (0131): the engine's navmesh path for a walking survivor,
	// found inside this one server frame. Vanilla declares these AIWorld calls
	// and never makes them (aiworld.c:98 and :122, world.c:116).
	protected bool DispatchNavPathQuery(MCPCommand command, MCPResult result)
	{
		World world;
		AIWorld aiWorld;
		PGFilter filter;
		vector fromPos;
		vector toPos;
		vector fromSnapped;
		vector toSnapped;
		vector waypoint;
		TVectorArray waypoints;
		bool found;
		float length;
		int i;

		if (!command.args || !IsFiniteVec3(command.args.from) || !IsFiniteVec3(command.args.to))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (!IsFiniteFloat(command.args.snap_radius) || command.args.snap_radius <= 0.0 || command.args.snap_radius > NAV_SNAP_RADIUS_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		world = GetGame().GetWorld();
		if (world)
		{
			aiWorld = world.GetAIWorld();
		}

		if (!aiWorld)
		{
			result.ok = false;
			result.error = "no_navmesh";
			return true;
		}

		fromPos = Vector(command.args.from.Get(0), command.args.from.Get(1), command.args.from.Get(2));
		toPos = Vector(command.args.to.Get(0), command.args.to.Get(1), command.args.to.Get(2));
		filter = BuildNavWalkFilter();
		// On false SampleNavmeshPosition leaves its output as it was
		// (aiworld.c:112-121), so that output is never read after a false.
		if (!aiWorld.SampleNavmeshPosition(fromPos, command.args.snap_radius, filter, fromSnapped))
		{
			result.ok = false;
			result.error = "no_navmesh_at_from";
			return true;
		}

		if (!aiWorld.SampleNavmeshPosition(toPos, command.args.snap_radius, filter, toSnapped))
		{
			result.ok = false;
			result.error = "no_navmesh_at_to";
			return true;
		}

		// The waypoints include both ends (aiworld.c:89-97).
		waypoints = new TVectorArray();
		found = aiWorld.FindPath(fromSnapped, toSnapped, filter, waypoints);
		if (!found || !waypoints || waypoints.Count() == 0)
		{
			result.ok = false;
			result.error = "no_path";
			return true;
		}

		result.points = new array<float>();
		length = 0.0;
		i = 0;
		while (i < waypoints.Count())
		{
			waypoint = waypoints.Get(i);
			result.points.Insert(waypoint[0]);
			result.points.Insert(waypoint[1]);
			result.points.Insert(waypoint[2]);
			if (i > 0)
			{
				length = length + vector.Distance(waypoints.Get(i - 1), waypoint);
			}

			i = i + 1;
		}

		result.length_m = length;
		result.from_snapped = new array<float>();
		VectorToArray(fromSnapped, result.from_snapped);
		result.to_snapped = new array<float>();
		VectorToArray(toSnapped, result.to_snapped);
		result.ok = true;
		return true;
	}

	// The filter nav_path_query walks with: the walking flags (ground, doors,
	// inside buildings) included, the disabled and swimming ones excluded.
	// PGPolyFlags declares no values although PGFilter takes bitmasks
	// (aiworld.c:1-26 and :61), and vanilla never builds a PGFilter: these
	// masks are not measured. The final in-game round's street, door and
	// water paths test what they admit, not their values.
	protected PGFilter BuildNavWalkFilter()
	{
		PGFilter filter = new PGFilter();
		int includeFlags = PGPolyFlags.WALK | PGPolyFlags.DOOR | PGPolyFlags.INSIDE;
		int excludeFlags = PGPolyFlags.DISABLED | PGPolyFlags.SWIM | PGPolyFlags.SWIM_SEA;
		filter.SetFlags(includeFlags, excludeFlags, 0);
		return filter;
	}

	// players_visibility (33c5): which players could see pos. The server does
	// not know a client's camera, so in_fov approximates it, generous on
	// purpose: a hidden edit fails safe when it over-reports.
	protected bool DispatchPlayersVisibility(MCPCommand command, MCPResult result)
	{
		vector target;
		vector head;
		float radius;
		float distance;
		PlayerBase player;
		PlayerIdentity identity;
		MCPPlayerVisibility row;
		int i;

		if (!command.args || !IsFiniteVec3(command.args.pos))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		radius = command.args.radius;
		if (!IsFiniteFloat(radius) || radius < 0.0 || radius > VISIBILITY_RADIUS_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		target = Vector(command.args.pos.Get(0), command.args.pos.Get(1), command.args.pos.Get(2));
		result.players_visibility = new array<ref MCPPlayerVisibility>();
		m_Players.Clear();
		GetGame().GetPlayers(m_Players);
		i = 0;
		while (i < m_Players.Count())
		{
			player = PlayerBase.Cast(m_Players.Get(i));
			identity = null;
			if (player)
			{
				identity = player.GetIdentity();
			}

			if (identity)
			{
				head = PlayerHeadPosition(player);
				distance = vector.Distance(head, target);
				row = new MCPPlayerVisibility();
				row.uid = identity.GetPlainId();
				row.distance = distance;
				row.in_fov = IsInApproxFov(player, head, target, radius);
				row.line_of_sight = HasLineOfSight(player, head, target, radius);
				row.visible = row.in_fov && row.line_of_sight;
				result.players_visibility.Insert(row);
			}

			i = i + 1;
		}

		result.ok = true;
		return true;
	}

	// The head bone (GetBoneIndexByName, human.c:1417; GetBonePositionWS,
	// object.c:238). Without that bone, the position plus
	// VISIBILITY_HEAD_FALLBACK_M, a standing survivor's head: a crouched or
	// prone one is then read high.
	// Vanilla's MiscGameplayFunctions.GetHeadBonePos uses the same bone and
	// fallback, but its fallback assigns a float to the vector
	// (miscgameplayfunctions.c:751), so the bridge keeps its own.
	protected vector PlayerHeadPosition(PlayerBase player)
	{
		int bone = player.GetBoneIndexByName(VISIBILITY_HEAD_BONE);
		if (bone < 0)
		{
			return player.GetPosition() + Vector(0.0, VISIBILITY_HEAD_FALLBACK_M, 0.0);
		}

		return player.GetBonePositionWS(bone);
	}

	// Horizontal only, generous on purpose: in view when any part of the
	// sphere of radius around pos lies within VISIBILITY_HALF_FOV_DEG of the
	// heading GetHeadingAngle gives (human.c:27-28: the world Y angle of the
	// camera, in radians). horizontal is the head-to-pos distance in x and z.
	// Within radius the sphere's footprint holds the head, so pos is in view
	// whatever the heading, straight above or below included. Beyond it the
	// footprint spans Math.Asin(radius / horizontal) either side of the
	// bearing, and the cone widens by exactly that. Compass degrees, 0 = +Z
	// and 90 = +X, as MCP_PlayerMove.c computes them: the heading h is -h,
	// and the bearing is Math.Atan2(dx, dz) (enmath.c:388-399). Nothing says
	// that heading is valid on the server. Also in view when it cannot be read.
	protected bool IsInApproxFov(PlayerBase player, vector head, vector target, float radius)
	{
		HumanInputController hic;
		float dx;
		float dz;
		float horizontal;
		float bearingDeg;
		float headingDeg;
		float offDeg;
		float allowedDeg;

		dx = target[0] - head[0];
		dz = target[2] - head[2];
		horizontal = Math.Sqrt(dx * dx + dz * dz);
		if (horizontal <= radius)
		{
			return true;
		}

		hic = player.GetInputController();
		if (!hic)
		{
			return true;
		}

		bearingDeg = Math.Atan2(dx, dz) * Math.RAD2DEG;
		headingDeg = hic.GetHeadingAngle();
		headingDeg = -headingDeg * Math.RAD2DEG;
		// Both are -180..180, so one turn either way brings the difference there.
		offDeg = bearingDeg - headingDeg;
		if (offDeg > 180.0)
		{
			offDeg = offDeg - 360.0;
		}

		if (offDeg < -180.0)
		{
			offDeg = offDeg + 360.0;
		}

		if (offDeg < 0.0)
		{
			offDeg = -offDeg;
		}

		// Past the early return, horizontal > radius >= 0: Asin (enmath.c:367) gets [0, 1).
		allowedDeg = VISIBILITY_HALF_FOV_DEG + Math.Asin(radius / horizontal) * Math.RAD2DEG;
		return offDeg <= allowedDeg;
	}

	// scene_raycast's default ray (rvproxy, view geometry, VISIBILITY_RAY_RADIUS)
	// from the head to pos, ignoring the player's own entity. Clear when
	// nothing is hit or the nearest hit is within radius of pos. The ray goes
	// to pos only, so a volume of radius around pos can be partly seen while
	// this is false: only IsInApproxFov covers the whole volume.
	protected bool HasLineOfSight(PlayerBase player, vector head, vector target, float radius)
	{
		MCPRaycastValidation ray = new MCPRaycastValidation();
		MCPRaycastHit hit = new MCPRaycastHit();
		vector hitPos;

		ray.from = head;
		ray.to = target;
		ray.radius = VISIBILITY_RAY_RADIUS;
		ray.intersect_type = ObjIntersectView;
		PopulateRaycastRVProxy(ray, player, hit);
		if (!hit.hit)
		{
			return true;
		}

		hitPos = Vector(hit.pos.Get(0), hit.pos.Get(1), hit.pos.Get(2));
		return vector.Distance(hitPos, target) <= radius;
	}

	protected bool IsFiniteVec3(array<float> values)
	{
		if (!values || values.Count() != 3)
		{
			return false;
		}

		return IsFiniteFloat(values.Get(0)) && IsFiniteFloat(values.Get(1)) && IsFiniteFloat(values.Get(2));
	}

	protected bool DispatchWorldWeatherSet(MCPCommand command, MCPResult result)
	{
		if (!ValidateWorldWeatherArgs(command.args, result))
		{
			return true;
		}

		Weather weather = GetGame().GetWeather();
		if (!weather)
		{
			result.ok = false;
			result.error = "weather_unavailable";
			return true;
		}

		Overcast overcast = weather.GetOvercast();
		Rain rain = weather.GetRain();
		Fog fog = weather.GetFog();
		float changeTime = command.args.time;
		float minDuration = command.args.min_duration;

		if (command.args.overcast_set)
		{
			overcast.Set(command.args.overcast, changeTime, minDuration);
		}
		if (command.args.rain_set)
		{
			rain.Set(command.args.rain, changeTime, minDuration);
		}
		if (command.args.fog_set)
		{
			fog.Set(command.args.fog, changeTime, minDuration);
		}

		MCPApplied applied = new MCPApplied();
		applied.overcast_actual = overcast.GetActual();
		applied.rain_actual = rain.GetActual();
		applied.fog_actual = fog.GetActual();
		applied.overcast_forecast = overcast.GetForecast();
		applied.rain_forecast = rain.GetForecast();
		applied.fog_forecast = fog.GetForecast();
		result.applied = applied;
		result.ok = true;
		return true;
	}

	protected bool DispatchExecEnforce(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.expr == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		bool sent = GetGame().ExecuteEnforceScript(command.args.expr, command.args.main_fn);
		if (!sent)
		{
			result.ok = false;
			result.error = "exec_failed";
			return true;
		}

		result.ok = true;
		result.sent = true;
		return true;
	}

	protected MCPRaycastValidation ValidateRaycastArgs(MCPArgs args)
	{
		MCPRaycastValidation validation = new MCPRaycastValidation();
		validation.ok = false;
		validation.radius = 0.05;
		validation.intersect_type = ObjIntersectView;

		if (!args)
		{
			validation.error = "bad_args";
			return validation;
		}

		if (!args.from || args.from.Count() != 3 || !args.to || args.to.Count() != 3)
		{
			validation.error = "bad_args";
			return validation;
		}

		float fromX = args.from.Get(0);
		float fromY = args.from.Get(1);
		float fromZ = args.from.Get(2);
		float toX = args.to.Get(0);
		float toY = args.to.Get(1);
		float toZ = args.to.Get(2);
		if (!IsFiniteFloat(fromX) || !IsFiniteFloat(fromY) || !IsFiniteFloat(fromZ) || !IsFiniteFloat(toX) || !IsFiniteFloat(toY) || !IsFiniteFloat(toZ))
		{
			validation.error = "bad_args";
			return validation;
		}

		if (!IsFiniteFloat(args.radius) || args.radius < 0.0 || args.radius > RAYCAST_MAX_RADIUS)
		{
			validation.error = "bad_args";
			return validation;
		}
		if (args.radius > 0.0)
		{
			validation.radius = args.radius;
		}

		string method = args.method;
		if (method == "")
		{
			method = "rvproxy";
		}
		if (method != "rvproxy" && method != "bullet")
		{
			validation.error = "bad_args";
			return validation;
		}

		string intersect = args.intersect;
		if (intersect == "")
		{
			intersect = "view";
		}
		if (intersect == "view")
		{
			validation.intersect_type = ObjIntersectView;
		}
		else if (intersect == "fire")
		{
			validation.intersect_type = ObjIntersectFire;
		}
		else if (intersect == "geom")
		{
			validation.intersect_type = ObjIntersectGeom;
		}
		else if (intersect == "ifire")
		{
			validation.intersect_type = ObjIntersectIFire;
		}
		else
		{
			validation.error = "bad_args";
			return validation;
		}

		if (args.ignore != "" && args.ignore != "player")
		{
			validation.error = "bad_args";
			return validation;
		}

		validation.from = Vector(fromX, fromY, fromZ);
		validation.to = Vector(toX, toY, toZ);
		validation.ok = true;
		return validation;
	}

	protected MCPTelemetryValidation ValidateTelemetryArgs(MCPArgs args)
	{
		MCPTelemetryValidation validation = new MCPTelemetryValidation();
		validation.ok = false;
		validation.max_lines = TELEMETRY_JSONL_DEFAULT_MAX_LINES;

		if (!args)
		{
			validation.error = "bad_args";
			return validation;
		}

		validation.mode = args.mode;
		if (validation.mode == "object_at")
		{
			if (args.type == "" || !args.pos || args.pos.Count() != 3 || args.radius <= 0.0 || !IsFiniteFloat(args.radius) || args.radius > TELEMETRY_OBJECT_AT_MAX_RADIUS)
			{
				validation.error = "bad_args";
				return validation;
			}

			float x = args.pos.Get(0);
			float y = args.pos.Get(1);
			float z = args.pos.Get(2);
			if (!IsFiniteFloat(x) || !IsFiniteFloat(y) || !IsFiniteFloat(z))
			{
				validation.error = "bad_args";
				return validation;
			}

			validation.type = args.type;
			validation.pos = Vector(x, y, z);
			validation.radius = args.radius;
			validation.ok = true;
			return validation;
		}

		if (validation.mode == "fixture_jsonl")
		{
			string prefix = "$mission:dayz_mcp/";
			if (!StringHasPrefix(args.path, prefix))
			{
				validation.error = "bad_args";
				return validation;
			}

			int prefixLength = prefix.Length();
			int pathLeafLength = args.path.Length() - prefixLength;
			if (pathLeafLength <= 0)
			{
				validation.error = "bad_args";
				return validation;
			}

			string pathLeaf = args.path.Substring(prefixLength, pathLeafLength);
			for (int pathIndex = 0; pathIndex < pathLeaf.Length(); pathIndex = pathIndex + 1)
			{
				string pathChar = pathLeaf.Substring(pathIndex, 1);
				if (pathChar == "/" || pathChar == "\\")
				{
					validation.error = "bad_args";
					return validation;
				}

				if (pathChar == "." && pathIndex + 1 < pathLeaf.Length())
				{
					string nextPathChar = pathLeaf.Substring(pathIndex + 1, 1);
					if (nextPathChar == ".")
					{
						validation.error = "bad_args";
						return validation;
					}
				}
			}

			if (args.max_lines < 0)
			{
				validation.error = "bad_args";
				return validation;
			}
			if (args.max_lines > 0)
			{
				validation.max_lines = args.max_lines;
				if (validation.max_lines > TELEMETRY_JSONL_DEFAULT_MAX_LINES)
				{
					validation.max_lines = TELEMETRY_JSONL_DEFAULT_MAX_LINES;
				}
			}

			validation.path = args.path;
			validation.ok = true;
			return validation;
		}

		validation.error = "bad_args";
		return validation;
	}

	protected bool ValidateWorldTimeArgs(MCPArgs args, MCPResult result)
	{
		if (!args)
		{
			result.ok = false;
			result.error = "bad_args";
			return false;
		}

		if (args.year < 1970 || args.year > 2100)
		{
			result.ok = false;
			result.error = "bad_year";
			return false;
		}
		if (args.month < 1 || args.month > 12)
		{
			result.ok = false;
			result.error = "bad_month";
			return false;
		}
		if (args.day < 1 || args.day > 31)
		{
			result.ok = false;
			result.error = "bad_day";
			return false;
		}
		if (args.hour < 0 || args.hour > 23)
		{
			result.ok = false;
			result.error = "bad_hour";
			return false;
		}
		if (args.minute < 0 || args.minute > 59)
		{
			result.ok = false;
			result.error = "bad_minute";
			return false;
		}
		if (args.time_multiplier_set)
		{
			if (!IsFiniteFloat(args.time_multiplier))
			{
				result.ok = false;
				result.error = "bad_time_multiplier";
				return false;
			}
			if (args.time_multiplier != -1.0 && (args.time_multiplier < 0.0 || args.time_multiplier > 64.0))
			{
				result.ok = false;
				result.error = "bad_time_multiplier";
				return false;
			}
		}

		return true;
	}

	protected bool ValidateWorldWeatherArgs(MCPArgs args, MCPResult result)
	{
		if (!args)
		{
			result.ok = false;
			result.error = "bad_args";
			return false;
		}

		if (!IsFiniteFloat(args.time) || !IsFiniteFloat(args.min_duration))
		{
			result.ok = false;
			result.error = "bad_args";
			return false;
		}
		if (args.time < 0.0 || args.min_duration < 0.0)
		{
			result.ok = false;
			result.error = "bad_args";
			return false;
		}

		bool hasPhenomenon = false;
		if (args.overcast_set)
		{
			hasPhenomenon = true;
			if (!ValidateWeatherValue(args.overcast))
			{
				result.ok = false;
				result.error = "bad_overcast";
				return false;
			}
		}
		if (args.rain_set)
		{
			hasPhenomenon = true;
			if (!ValidateWeatherValue(args.rain))
			{
				result.ok = false;
				result.error = "bad_rain";
				return false;
			}
		}
		if (args.fog_set)
		{
			hasPhenomenon = true;
			if (!ValidateWeatherValue(args.fog))
			{
				result.ok = false;
				result.error = "bad_fog";
				return false;
			}
		}
		if (!hasPhenomenon)
		{
			result.ok = false;
			result.error = "no_weather_fields";
			return false;
		}

		return true;
	}

	protected bool ValidateWeatherValue(float value)
	{
		if (!IsFiniteFloat(value))
		{
			return false;
		}

		if (value < 0.0 || value > 1.0)
		{
			return false;
		}

		return true;
	}

	protected string GetPollVersion()
	{
		string gameVersion;
		string pollVersion;
		bool cacheVersion = true;

		if (m_PollVersion != "")
		{
			return m_PollVersion;
		}

		g_Game.GetVersion(gameVersion);
		if (gameVersion == "")
		{
			gameVersion = "unknown";
			cacheVersion = false;
		}
		pollVersion = MCP_BRIDGE_VERSION;
		pollVersion = pollVersion + "~";
		pollVersion = pollVersion + gameVersion;
		pollVersion = EncodeQueryValue(pollVersion);
		if (cacheVersion)
		{
			m_PollVersion = pollVersion;
		}
		return pollVersion;
	}

	protected string EncodeQueryValue(string value)
	{
		string encoded = "";
		string hexDigits = "0123456789ABCDEF";
		string character = "";
		int asciiCode = 0;
		int highNibble = 0;
		int lowNibble = 0;
		bool unreserved = false;
		int i = 0;
		while (i < value.Length())
		{
			character = value.Substring(i, 1);
			asciiCode = character.ToAscii();
			unreserved = false;
			if (asciiCode >= 65 && asciiCode <= 90)
			{
				unreserved = true;
			}
			else if (asciiCode >= 97 && asciiCode <= 122)
			{
				unreserved = true;
			}
			else if (asciiCode >= 48 && asciiCode <= 57)
			{
				unreserved = true;
			}
			else if (asciiCode == 45 || asciiCode == 46 || asciiCode == 95 || asciiCode == 126)
			{
				unreserved = true;
			}

			if (unreserved)
			{
				encoded = encoded + character;
			}
			else
			{
				if (asciiCode < 0 || asciiCode > 255)
				{
					asciiCode = 63;
				}
				highNibble = asciiCode / 16;
				lowNibble = asciiCode - (highNibble * 16);
				encoded = encoded + "%";
				encoded = encoded + hexDigits.Substring(highNibble, 1);
				encoded = encoded + hexDigits.Substring(lowNibble, 1);
			}

			i = i + 1;
		}

		return encoded;
	}

	protected Object ResolveRaycastIgnore(MCPArgs args)
	{
		if (!args || args.ignore != "player")
		{
			return null;
		}

		m_Players.Clear();
		GetGame().GetPlayers(m_Players);
		if (m_Players.Count() == 0)
		{
			return null;
		}

		return m_Players.Get(0);
	}

	protected void PopulateRaycastRVProxy(MCPRaycastValidation validation, Object ignore, MCPRaycastHit hit)
	{
		RaycastRVParams rvParams = new RaycastRVParams(validation.from, validation.to, ignore, validation.radius);
		rvParams.type = validation.intersect_type;

		array<ref RaycastRVResult> results = new array<ref RaycastRVResult>();
		bool ok = DayZPhysics.RaycastRVProxy(rvParams, results);
		int bestIndex = -1;
		float bestDistanceSq = float.MAX;
		int i = 0;
		while (i < results.Count())
		{
			RaycastRVResult current = results.Get(i);
			if (current)
			{
				float distanceSq = vector.DistanceSq(validation.from, current.pos);
				if (distanceSq < bestDistanceSq)
				{
					bestDistanceSq = distanceSq;
					bestIndex = i;
				}
			}

			i = i + 1;
		}

		if (!ok || bestIndex < 0)
		{
			hit.hit = false;
			return;
		}

		RaycastRVResult rayHit = results.Get(bestIndex);
		if (!rayHit)
		{
			hit.hit = false;
			return;
		}

		hit.hit = true;
		VectorToArray(rayHit.pos, hit.pos);
		VectorToArray(rayHit.dir, hit.normal);
		hit.distance = vector.Distance(validation.from, rayHit.pos);
		hit.component = rayHit.component;
		hit.hier_level = rayHit.hierLevel;
		hit.entry = rayHit.entry;
		hit.exit = rayHit.exit;

		if (rayHit.obj)
		{
			hit.object_type = rayHit.obj.GetType();
			hit.object_class = rayHit.obj.ClassName();
		}

		if (rayHit.hierLevel > 0 && rayHit.parent)
		{
			hit.parent_type = rayHit.parent.GetType();
		}

		if (rayHit.surface)
		{
			hit.surface_name = rayHit.surface.GetName();
			hit.surface_type = rayHit.surface.GetSurfaceType();
		}
	}

	protected void PopulateRaycastBullet(MCPRaycastValidation validation, Object ignore, MCPRaycastHit hit)
	{
		PhxInteractionLayers collisionLayerMask = PhxInteractionLayers.BUILDING|PhxInteractionLayers.DOOR|PhxInteractionLayers.VEHICLE|PhxInteractionLayers.ROADWAY|PhxInteractionLayers.TERRAIN|PhxInteractionLayers.ITEM_SMALL|PhxInteractionLayers.ITEM_LARGE|PhxInteractionLayers.FENCE;
		Object hitObject = null;
		vector hitPosition;
		vector hitNormal;
		float hitFraction;
		bool ok = DayZPhysics.RayCastBullet(validation.from, validation.to, collisionLayerMask, ignore, hitObject, hitPosition, hitNormal, hitFraction);
		hit.hit = ok;
		if (!ok)
		{
			return;
		}

		VectorToArray(hitPosition, hit.pos);
		VectorToArray(hitNormal, hit.normal);
		hit.distance = hitFraction * vector.Distance(validation.from, validation.to);
		if (hitObject)
		{
			hit.object_type = hitObject.GetType();
			hit.object_class = hitObject.ClassName();
		}
	}

	protected bool DispatchTelemetryObjectAt(MCPTelemetryValidation validation, MCPTelemetry telemetry, MCPResult result)
	{
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(validation.pos, validation.radius, m_ReadyObjects, m_ReadyProxyCargos);

		Object match = null;
		int matchCount = 0;
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found && found.GetType() == validation.type)
			{
				matchCount = matchCount + 1;
				if (matchCount == 1)
				{
					match = found;
				}
			}

			i = i + 1;
		}

		if (matchCount == 0)
		{
			telemetry.found = false;
			result.ok = true;
			result.telemetry = telemetry;
			return true;
		}

		if (matchCount > 1)
		{
			result.ok = false;
			result.error = "ambiguous_fixture";
			result.telemetry = telemetry;
			return true;
		}

		PopulateTelemetryObject(match, telemetry);
		// Top-level lifetime for this EntityAI. A non-EntityAI omits it.
		// Zero remaining_s or max_s is a real reading (entityai.c:3380, :3387).
		EntityAI lifetimeEntity = EntityAI.Cast(match);
		if (lifetimeEntity)
		{
			result.lifetime = new MCPSpawnLifetime();
			result.lifetime.remaining_s = lifetimeEntity.GetLifetime();
			result.lifetime.max_s = lifetimeEntity.GetLifetimeMax();
		}
		result.ok = true;
		result.telemetry = telemetry;
		return true;
	}

	protected bool DispatchTelemetryFixtureJsonl(MCPTelemetryValidation validation, MCPTelemetry telemetry, MCPResult result)
	{
		FileHandle handle = OpenFile(validation.path, FileMode.READ);
		if (handle == 0)
		{
			telemetry.found = false;
			result.ok = false;
			result.error = "fixture_not_found";
			result.telemetry = telemetry;
			return true;
		}

		JsonSerializer serializer = new JsonSerializer();
		string line;
		string parseError;
		MCPTelemetryFixtureLine parsed = null;
		bool parsedOk = false;
		int parsedIndex = 0;
		int readCount = FGets(handle, line);
		while (readCount >= 0 && telemetry.line_count_read < validation.max_lines)
		{
			parsedIndex = telemetry.line_count_read;
			telemetry.line_count_read = telemetry.line_count_read + 1;
			if (readCount > MAX_JSONL_LINE_CHARS)
			{
				CloseFile(handle);
				telemetry.parse_error = "line_too_long";
				result.ok = false;
				result.error = "parse_error";
				result.telemetry = telemetry;
				return true;
			}

			parsed = m_TelemetryFixtureLinePool.Get(parsedIndex);
			parsed.Reset();
			parseError = "";
			parsedOk = serializer.ReadFromString(parsed, line, parseError);
			if (!parsedOk)
			{
				CloseFile(handle);
				telemetry.parse_error = parseError;
				result.ok = false;
				result.error = "parse_error";
				result.telemetry = telemetry;
				return true;
			}
			if (parsed.fixture_id == "" || !IsFiniteFloat(parsed.value) || parsed.seq == MCP_FIXTURE_SEQ_UNSET)
			{
				CloseFile(handle);
				telemetry.parse_error = "schema_error";
				result.ok = false;
				result.error = "parse_error";
				result.telemetry = telemetry;
				return true;
			}

			telemetry.last_valid.fixture_id = parsed.fixture_id;
			telemetry.last_valid.value = parsed.value;
			telemetry.last_valid.seq = parsed.seq;
			readCount = FGets(handle, line);
		}

		CloseFile(handle);
		if (telemetry.line_count_read == 0)
		{
			telemetry.found = false;
			telemetry.parse_error = "empty_fixture";
			result.ok = false;
			result.error = "parse_error";
			result.telemetry = telemetry;
			return true;
		}
		telemetry.found = true;
		result.ok = true;
		result.telemetry = telemetry;
		return true;
	}

	protected void PopulateTelemetryObject(Object found, MCPTelemetry telemetry)
	{
		telemetry.found = true;
		telemetry.type = found.GetType();
		telemetry.class_name = found.ClassName();
		VectorToArray(found.GetPosition(), telemetry.pos);
		VectorToArray(found.GetOrientation(), telemetry.orientation);
		VectorToArray(found.GetDirection(), telemetry.direction);
		VectorToArray(GetVelocity(found), telemetry.velocity);
		string empty = "";
		telemetry.health01 = found.GetHealth01(empty, empty);

		EntityAI entity = EntityAI.Cast(found);
		if (entity)
		{
			PopulateTelemetryInventory(entity, telemetry);
		}

		Car car = Car.Cast(found);
		if (car)
		{
			telemetry.engine_on_server = car.EngineIsOn();
			telemetry.speedo = car.GetSpeedometer();
			telemetry.wheel_count = car.WheelCountPresent();
			telemetry.fuel_fraction = car.GetFluidFraction(CarFluid.FUEL);
		}
	}

	protected void PopulateTelemetryInventory(EntityAI entity, MCPTelemetry telemetry)
	{
		GameInventory inventory = entity.GetInventory();
		int i = 0;
		int slotCount = 0;
		int slotId = 0;
		string slotName = "";
		EntityAI attachment = null;
		CargoBase cargo = null;
		EntityAI cargoItem = null;
		if (!inventory)
		{
			return;
		}

		// The attachment-slot family, not the belongs-to family.
		// GetSlotIdCount/GetSlotId report the slots THIS entity can be attached
		// INTO (inventory.c:171,175), so a CivilianSedan -- which attaches to no
		// parent -- yielded one invalid id and GetSlotName returned "".
		// Vanilla precedent for this exact pair: HasAttachmentSlot, inventory.c:188-193.
		slotCount = inventory.GetAttachmentSlotsCount();
		while (i < slotCount)
		{
			slotId = inventory.GetAttachmentSlotId(i);
			slotName = InventorySlots.GetSlotName(slotId);
			telemetry.declared_slots.Insert(slotName);
			i = i + 1;
		}

		telemetry.attachment_count = inventory.AttachmentCount();
		telemetry.items_total = telemetry.attachment_count;
		telemetry.items_truncated = false;
		if (telemetry.items_total > TELEMETRY_ITEMS_CAP)
		{
			telemetry.items_truncated = true;
		}
		i = 0;
		while (i < telemetry.attachment_count && telemetry.attachment_items.Count() < TELEMETRY_ITEMS_CAP)
		{
			attachment = inventory.GetAttachmentFromIndex(i);
			if (attachment)
			{
				telemetry.attachment_items.Insert(attachment.GetType());
				if (telemetry.items.Count() < TELEMETRY_ITEMS_CAP)
				{
					telemetry.items.Insert(attachment.GetType());
				}
			}

			i = i + 1;
		}

		cargo = inventory.GetCargo();
		if (!cargo)
		{
			return;
		}

		telemetry.cargo_count = cargo.GetItemCount();
		telemetry.items_total = telemetry.items_total + telemetry.cargo_count;
		if (telemetry.items_total > TELEMETRY_ITEMS_CAP)
		{
			telemetry.items_truncated = true;
		}
		i = 0;
		while (i < telemetry.cargo_count && telemetry.cargo_items.Count() < TELEMETRY_ITEMS_CAP)
		{
			cargoItem = cargo.GetItem(i);
			if (cargoItem)
			{
				telemetry.cargo_items.Insert(cargoItem.GetType());
				if (telemetry.items.Count() < TELEMETRY_ITEMS_CAP)
				{
					telemetry.items.Insert(cargoItem.GetType());
				}
			}

			i = i + 1;
		}
	}

	protected void VectorToArray(vector v, array<float> a)
	{
		a.Clear();
		a.Insert(v[0]);
		a.Insert(v[1]);
		a.Insert(v[2]);
	}

	protected bool StringHasPrefix(string value, string prefix)
	{
		if (value.Length() < prefix.Length())
		{
			return false;
		}

		if (value.Substring(0, prefix.Length()) == prefix)
		{
			return true;
		}

		return false;
	}

	// fb-20260822-191204-46b3: the only bridge URL is "http://127.0.0.1:<port>/",
	// the form the daemon and both installers write. It is the RestContext base
	// that "poll?..." and "result?..." are appended to, so the final "/" belongs
	// to it (see PollContextUrl in MCPClientBridge.c). <port> is 1 to 5 ASCII
	// digits (ToAscii 48..57) worth 1..65535. Anything else, userinfo, path,
	// query, fragment, whitespace or backslash included, is refused. The same
	// rule is in MCPClientBridge.c.
	protected bool IsLoopbackBridgeUrl(string url)
	{
		string prefix = "http://127.0.0.1:";
		int prefixLength = prefix.Length();
		int digitCount = url.Length() - prefixLength - 1;
		int port = 0;
		int index = 0;
		int code = 0;
		string digit;

		if (digitCount < 1 || digitCount > 5)
		{
			return false;
		}

		if (!StringHasPrefix(url, prefix))
		{
			return false;
		}

		if (url.Substring(prefixLength + digitCount, 1) != "/")
		{
			return false;
		}

		while (index < digitCount)
		{
			digit = url.Substring(prefixLength + index, 1);
			code = digit.ToAscii();
			if (code < 48 || code > 57)
			{
				return false;
			}

			port = port * 10 + code - 48;
			index = index + 1;
		}

		if (port < 1 || port > 65535)
		{
			return false;
		}

		return true;
	}

	protected bool IsFiniteFloat(float value)
	{
		if (value != value)
		{
			return false;
		}
		if (value >= float.MAX || value <= -float.MAX)
		{
			return false;
		}

		return true;
	}

	protected MCPSpawnValidation ValidateSpawnArgs(MCPArgs args)
	{
		MCPSpawnValidation validation = new MCPSpawnValidation();
		validation.ok = false;
		validation.flags = ECE_PLACE_ON_SURFACE;
		validation.rotation = RF_DEFAULT;

		if (!args)
		{
			validation.error = "bad_args";
			return validation;
		}

		if (args.type == "")
		{
			validation.error = "unknown_type";
			return validation;
		}

		string cfgPath = "CfgVehicles ";
		cfgPath = cfgPath + args.type;
		if (!GetGame().ConfigIsExisting(cfgPath))
		{
			validation.error = "unknown_type";
			return validation;
		}

		if (!args.pos || args.pos.Count() != 3)
		{
			validation.error = "bad_pos";
			return validation;
		}

		float x = args.pos.Get(0);
		float y = args.pos.Get(1);
		float z = args.pos.Get(2);
		float worldSize = GetGame().GetWorld().GetWorldSize();
		if (!IsFiniteWorldCoord(x, worldSize) || !IsFiniteWorldCoord(y, worldSize) || !IsFiniteWorldCoord(z, worldSize))
		{
			validation.error = "bad_pos";
			return validation;
		}

		// y == 0 means "on the ground" (same contract as player_teleport). Resolving it here keeps
		// pos_real honest for entities whose surface placement is deferred to the first sim frame
		// (AI spawned with ECE_INITAI): the readiness probe reads GetPosition() before that frame runs.
		if (y == 0)
		{
			y = GetGame().SurfaceY(x, z);
		}

		if (args.flags != 0)
		{
			if (!IsAllowedSpawnFlags(args.flags))
			{
				validation.error = "bad_flags";
				return validation;
			}

			validation.flags = args.flags;
		}

		if (args.rotation != 0)
		{
			validation.rotation = args.rotation;
		}

		// fb-20260930-080543-bd28: checked here, before CreateObjectEx, so a bad
		// lifetime_s spawns nothing. Only the flag asks for the override (see
		// MCPArgs); IsFiniteFloat refuses NaN, which the range test alone would pass.
		if (args.lifetime_s_set)
		{
			if (!IsFiniteFloat(args.lifetime_s) || args.lifetime_s <= 0.0 || args.lifetime_s > SPAWN_LIFETIME_MAX_S)
			{
				validation.error = "bad_lifetime";
				return validation;
			}
		}

		validation.pos = Vector(x, y, z);
		validation.ok = true;
		return validation;
	}

	// fb-20260930-080543-bd28: the mission's CE deletes an entity whose economy
	// lifetime has run out once no player is within CleanupAvoidance (100 m in
	// dayzOffline.chernarusplus db/globals.xml), and CivilianSedan's types.xml
	// lifetime is 3 s. lifetime_s overrides that lifetime on the entity world_spawn
	// has just created, in the same call: Dispatch runs this as soon as
	// DispatchWorldSpawn has queued the spawn job, because that method is a pinned
	// region (tests/test_task9_spawn_phase_markers.py). ValidateSpawnArgs has
	// already refused a value outside (0, SPAWN_LIFETIME_MAX_S]. SetLifetimeMax,
	// then SetLifetime (entityai.c:3377-3387), make lifetime_s both the entity's
	// own maximum and what remains of it. Those setters are EntityAI protos: an
	// object that is not an EntityAI has no economy lifetime to set, so it is
	// deleted and the command refused with lifetime_unsupported, rather than
	// answered ok for a lifetime that was never set. Returns true when that
	// refusal is to be posted now; false leaves the reply to the spawn job.
	protected bool ApplySpawnLifetime(MCPCommand command, MCPResult result)
	{
		MCPJob job;
		Object spawned;
		EntityAI entity;

		if (!command.args || !command.args.lifetime_s_set)
		{
			return false;
		}

		// DispatchWorldSpawn returns false only after queuing, under the command
		// id, a spawn job whose subject is the object it has just created. The job
		// is read, not m_RuntimeObjects: that registry is never cleared and the
		// daemon restarts its ids at 1, so an id can still name an older object.
		job = m_Jobs.Get(command.id);
		if (job)
		{
			spawned = job.subject;
		}
		entity = EntityAI.Cast(spawned);
		if (entity)
		{
			entity.SetLifetimeMax(command.args.lifetime_s);
			entity.SetLifetime(command.args.lifetime_s);
			Log("spawn lifetime set id=" + command.id + " lifetime_s=" + command.args.lifetime_s);
			return false;
		}

		m_Jobs.Remove(command.id);
		m_RuntimeObjects.Remove(command.id);
		if (spawned)
		{
			GetGame().ObjectDelete(spawned);
		}

		result.ok = false;
		result.error = "lifetime_unsupported";
		Log("spawn lifetime refused id=" + command.id + " reason=not_entityai");
		return true;
	}

	protected MCPSpawnValidation ValidatePositionArgs(MCPArgs args)
	{
		MCPSpawnValidation validation = new MCPSpawnValidation();
		validation.ok = false;

		if (!args)
		{
			validation.error = "bad_args";
			return validation;
		}

		if (!args.pos || args.pos.Count() != 3)
		{
			validation.error = "bad_pos";
			return validation;
		}

		float x = args.pos.Get(0);
		float y = args.pos.Get(1);
		float z = args.pos.Get(2);
		float worldSize = GetGame().GetWorld().GetWorldSize();
		if (!IsFiniteWorldCoord(x, worldSize) || !IsFiniteWorldCoord(y, worldSize) || !IsFiniteWorldCoord(z, worldSize))
		{
			validation.error = "bad_pos";
			return validation;
		}

		validation.pos = Vector(x, y, z);
		validation.ok = true;
		return validation;
	}

	protected bool IsFiniteWorldCoord(float value, float worldSize)
	{
		if (value != value)
		{
			return false;
		}

		if (value < 0.0)
		{
			return false;
		}

		if (value > worldSize)
		{
			return false;
		}

		return true;
	}

	protected bool IsAllowedSpawnFlags(int flags)
	{
		int noPathgraphFlags = ECE_CREATEPHYSICS | ECE_TRACE;
		if (flags == noPathgraphFlags)
		{
			return true;
		}

		if ((flags & ECE_PLACE_ON_SURFACE) != ECE_PLACE_ON_SURFACE)
		{
			return false;
		}

		int extraFlags = flags - ECE_PLACE_ON_SURFACE;
		int allowedExtraFlags = ECE_INITAI | ECE_EQUIP_ATTACHMENTS | ECE_NOPERSISTENCY_WORLD | ECE_CREATEPHYSICS;
		if ((extraFlags | allowedExtraFlags) == allowedExtraFlags)
		{
			return true;
		}

		return false;
	}

	protected Human GetFirstHuman()
	{
		m_Players.Clear();
		GetGame().GetPlayers(m_Players);

		if (m_Players.Count() == 0)
		{
			return null;
		}

		Man player = m_Players.Get(0);
		if (!player)
		{
			return null;
		}

		return Human.Cast(player);
	}

	protected Human FindHumanByUid(string uid)
	{
		if (uid == "")
		{
			return null;
		}

		m_Players.Clear();
		GetGame().GetPlayers(m_Players);

		int i = 0;
		while (i < m_Players.Count())
		{
			Man p = m_Players.Get(i);
			if (p)
			{
				PlayerIdentity ident = p.GetIdentity();
				if (ident && ident.GetPlainId() == uid)
				{
					return Human.Cast(p);
				}
			}

			i = i + 1;
		}

		return null;
	}

	protected PlayerBase ResolvePlayer(MCPArgs args, out string error)
	{
		error = "";
		Human human = null;
		if (!args || args.uid == "")
		{
			human = GetFirstHuman();
			if (!human)
			{
				error = "no_players";
				return null;
			}
		}
		else
		{
			human = FindHumanByUid(args.uid);
			if (!human)
			{
				error = "player_not_found";
				return null;
			}
		}

		PlayerBase player = PlayerBase.Cast(human);
		if (!player)
		{
			if (args && args.uid != "")
			{
				error = "player_not_found";
			}
			else
			{
				error = "no_players";
			}

			return null;
		}

		return player;
	}

	protected array<ref MCPEntityHit> TakeNearestEntities(array<ref MCPEntityHit> collected, int limit)
	{
		array<ref MCPEntityHit> nearest = new array<ref MCPEntityHit>();
		if (!collected || limit <= 0)
		{
			return nearest;
		}

		while (collected.Count() > 0 && nearest.Count() < limit)
		{
			int minIdx = 0;
			int k = 1;
			while (k < collected.Count())
			{
				if (collected.Get(k).distance < collected.Get(minIdx).distance)
				{
					minIdx = k;
				}

				k = k + 1;
			}

			nearest.Insert(collected.Get(minIdx));
			collected.Remove(minIdx);
		}

		return nearest;
	}

	protected Transport FindTransportNear(vector pos)
	{
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, VEHICLE_SEARCH_RADIUS, m_ReadyObjects, m_ReadyProxyCargos);

		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			Transport vehicle = Transport.Cast(found);
			if (vehicle)
			{
				return vehicle;
			}

			i = i + 1;
		}

		return null;
	}

	protected bool HasActiveJobFor(Human actor, Object subject)
	{
		if (!m_Jobs)
		{
			return false;
		}

		int i = 0;
		while (i < m_Jobs.Count())
		{
			MCPJob job = m_Jobs.GetElement(i);
			if (job)
			{
				if (actor && job.actor == actor)
				{
					return true;
				}

				if (subject && job.subject == subject)
				{
					return true;
				}
			}

			i = i + 1;
		}

		return false;
	}

	protected void ProcessJobs()
	{
		if (!m_Jobs)
		{
			return;
		}

		int i = m_Jobs.Count() - 1;
		while (i >= 0)
		{
			int jobId = m_Jobs.GetKey(i);
			MCPJob job = m_Jobs.GetElement(i);
			if (!job)
			{
				m_Jobs.Remove(jobId);
			}
			else
			{
				if (job.kind == "inventory_move" && job.phase == 1)
				{
					PostJobSuccess(job);
					job.actor = null;
					job.subject = null;
					m_Jobs.Remove(jobId);
				}
				else if (IsJobReady(job))
				{
					PostJobSuccess(job);
					job.actor = null;
					job.subject = null;
					m_Jobs.Remove(jobId);
				}
				// A refusal stands only once the read above did not show the move.
				else if (job.kind == "inventory_move" && job.phase == 2)
				{
					MCPPostInventoryMoveRefused(job, job.error);
					job.actor = null;
					job.subject = null;
					m_Jobs.Remove(jobId);
				}
				else if (m_ElapsedS > job.deadline_s)
				{
					PostJobTimeout(job);
					job.actor = null;
					job.subject = null;
					m_Jobs.Remove(jobId);
				}
			}

			i = i - 1;
		}
	}

	protected bool IsJobReady(MCPJob job)
	{
		if (job.kind == "spawn")
		{
			return IsSpawnReady(job);
		}
		else if (job.kind == "seat")
		{
			return IsSeatReady(job);
		}
		else if (job.kind == "loadout_clear")
		{
			return IsLoadoutClearDone(job);
		}
		else if (job.kind == "inventory_move")
		{
			return MCPInventoryMoveReady(job);
		}
		else if (job.kind == "server_perf")
		{
			return IsServerPerfReady(job);
		}

		return false;
	}

	protected bool IsSpawnReady(MCPJob job)
	{
		if (!job.subject)
		{
			return false;
		}

		vector pos = job.subject.GetPosition();
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, SPAWN_READY_RADIUS, m_ReadyObjects, m_ReadyProxyCargos);

		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found && found == job.subject)
			{
				return true;
			}

			i = i + 1;
		}

		return false;
	}

	protected bool IsSeatReady(MCPJob job)
	{
		Human human = job.actor;
		if (!human)
		{
			human = GetFirstHuman();
			job.actor = human;
		}

		if (!human)
		{
			return false;
		}

		HumanCommandVehicle vehicleCommand = human.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			return false;
		}

		Transport transport = vehicleCommand.GetTransport();
		if (transport != job.subject)
		{
			return false;
		}

		if (vehicleCommand.IsGettingIn())
		{
			return false;
		}

		if (vehicleCommand.GetVehicleSeat() == DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			return true;
		}

		return false;
	}

	// One call per server frame: ProcessJobs runs from OnTick before the poll
	// gate, so the poll rate does not thin it out. The first call starts the
	// window at GetTickTime (game.c:914, seconds since start); each later call
	// counts one frame, filed under the whole second of the window it ends in.
	// The frame end that opens the window is the first of second 0, so each
	// whole second [s, s + 1) holds the frame ends inside it. A second counts
	// only when it ends inside the window, so a partial last second is dropped.
	protected bool IsServerPerfReady(MCPJob job)
	{
		float now = GetGame().GetTickTime();
		float elapsed;
		int second;

		if (job.phase == 0)
		{
			job.sample_start_s = now;
			job.perf_second_frames = 1;
			job.phase = 1;
			return false;
		}

		elapsed = now - job.sample_start_s;
		second = Math.Floor(elapsed);
		while (job.perf_second < second && job.perf_second + 1 <= job.sample_s_target)
		{
			if (job.perf_whole_seconds == 0 || job.perf_second_frames < job.perf_min_frames)
			{
				job.perf_min_frames = job.perf_second_frames;
			}

			job.perf_whole_seconds = job.perf_whole_seconds + 1;
			job.perf_second = job.perf_second + 1;
			job.perf_second_frames = 0;
		}

		job.perf_frames = job.perf_frames + 1;
		job.perf_second_frames = job.perf_second_frames + 1;
		job.perf_measured_s = elapsed;
		return elapsed >= job.sample_s_target;
	}

	protected bool IsVehicleFixtureReady(CarScript car)
	{
		if (!car)
		{
			return false;
		}

		if (car.WheelCountPresent() != car.WheelCount())
		{
			return false;
		}

		if (car.GetFluidFraction(CarFluid.FUEL) <= 0.0)
		{
			return false;
		}

		if (car.IsVitalCarBattery() || car.IsVitalTruckBattery())
		{
			if (!car.GetBattery())
			{
				return false;
			}
		}

		if (car.IsVitalSparkPlug())
		{
			string sparkSlot = "SparkPlug";
			if (!car.FindAttachmentBySlotName(sparkSlot))
			{
				return false;
			}
		}

		return true;
	}

	protected void PostJobSuccess(MCPJob job)
	{
		if (job.kind == "seat")
		{
			PostSeatSuccess(job);
			return;
		}
		if (job.kind == "loadout_clear")
		{
			PostLoadoutClearJob(job, true);
			return;
		}
		if (job.kind == "inventory_move")
		{
			MCPPostInventoryMove(job, true, "");
			return;
		}

		if (job.kind == "server_perf")
		{
			PostServerPerfSuccess(job);
			return;
		}

		MCPResult result = new MCPResult();
		result.id = job.id;
		result.ok = true;
		result.object_id = job.id;
		result.type = job.args.type;
		result.found = true;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		result.pos_real = new array<float>();

		vector pos = job.subject.GetPosition();
		result.pos_real.Insert(pos[0]);
		result.pos_real.Insert(pos[1]);
		result.pos_real.Insert(pos[2]);

		// fb-20260930-080543-bd28: what the entity reads now, so the caller sees the
		// override ApplySpawnLifetime set when the spawn was dispatched.
		if (job.args.lifetime_s_set)
		{
			EntityAI lifetimeEntity = EntityAI.Cast(job.subject);
			if (lifetimeEntity)
			{
				result.lifetime = new MCPSpawnLifetime();
				result.lifetime.remaining_s = lifetimeEntity.GetLifetime();
				result.lifetime.max_s = lifetimeEntity.GetLifetimeMax();
			}
		}
		PostResult(result);
	}

	protected void PostSeatSuccess(MCPJob job)
	{
		MCPResult result = new MCPResult();
		result.id = job.id;
		result.ok = true;
		result.seated = true;
		result.seat = "driver";
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		PostResult(result);
	}

	// After the window. insufficient_samples when it closed no whole second
	// or counted fewer than 2 frames; avg_fps and min_fps are then not written
	// and the tool drops them. The counts run after the last measured frame,
	// so their cost stays out of the window.
	protected void PostServerPerfSuccess(MCPJob job)
	{
		MCPResult result = new MCPResult();
		World world = GetGame().GetWorld();
		float frames = job.perf_frames;
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		if (!world)
		{
			result.ok = false;
			result.error = "world_unavailable";
			PostResult(result);
			return;
		}

		result.insufficient_samples = (job.perf_whole_seconds == 0 || job.perf_frames < 2);
		if (!result.insufficient_samples)
		{
			result.avg_fps = frames / job.perf_measured_s;
			result.min_fps = job.perf_min_frames;
		}

		result.counts = new MCPServerPerfCounts();
		CountServerPerfEntities(world.GetWorldSize(), result.counts);
		result.ok = true;
		PostResult(result);
	}

	// One SceneGetEntitiesInBox pass (dayzplayerutils.c:75, QueryFlags.DYNAMIC)
	// over the whole map. The query takes what touches or overlaps the box,
	// not what has its origin in it. Each entry it returns is counted once, in
	// the first class it matches (MCPServerPerfCounts); whether it can return
	// one entity twice is not measured.
	protected void CountServerPerfEntities(float worldSize, MCPServerPerfCounts counts)
	{
		array<EntityAI> entities = new array<EntityAI>();
		EntityAI entity;
		PlayerBase player;
		int i = 0;

		DayZPlayerUtils.SceneGetEntitiesInBox(Vector(0.0, -worldSize, 0.0), Vector(worldSize, worldSize, worldSize), entities, QueryFlags.DYNAMIC);
		while (i < entities.Count())
		{
			entity = entities.Get(i);
			player = PlayerBase.Cast(entity);
			if (player)
			{
				if (player.GetIdentity())
				{
					counts.players = counts.players + 1;
				}
				else
				{
					counts.ai = counts.ai + 1;
				}
			}
			else if (DayZInfected.Cast(entity))
			{
				counts.infected = counts.infected + 1;
			}
			else if (DayZAnimal.Cast(entity))
			{
				counts.animals = counts.animals + 1;
			}
			else if (Transport.Cast(entity))
			{
				counts.vehicles = counts.vehicles + 1;
			}
			else if (InventoryItem.Cast(entity))
			{
				counts.items = counts.items + 1;
			}

			i = i + 1;
		}
	}

	protected void PostJobFailure(MCPJob job)
	{
		MCPResult result = new MCPResult();
		result.id = job.id;
		result.ok = false;
		result.error = job.error;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		PostResult(result);
	}

	protected void PostJobTimeout(MCPJob job)
	{
		if (job.kind == "inventory_move")
		{
			MCPPostInventoryMove(job, false, "timeout");
			return;
		}
		MCPResult result = new MCPResult();
		// A loadout_clear job at its deadline reports, with cleanup_complete false.
		if (job.kind == "loadout_clear")
		{
			PostLoadoutClearJob(job, false);
			return;
		}
		result.id = job.id;
		result.ok = false;
		result.error = "timeout";
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		if (job.kind == "spawn")
		{
			result.object_id = job.id;
			if (job.args)
			{
				result.type = job.args.type;
			}

			if (job.subject)
			{
				if (result.type == "")
				{
					result.type = job.subject.GetType();
				}

				result.pos_real = new array<float>();
				VectorToArray(job.subject.GetPosition(), result.pos_real);
			}
		}

		PostResult(result);
	}

	protected void PostCommandError(MCPCommand command, string error)
	{
		MCPResult result = new MCPResult();
		result.id = command.id;
		result.ok = false;
		result.error = error;
		result.tick_poll_sent = m_TickPollSent;
		result.tick_poll_callback = m_TickPollCallback;
		result.tick_dispatch = m_Tick;
		PostResult(result);
	}

	protected array<ref MCPAllPlayer> BuildAllPlayers()
	{
		array<ref MCPAllPlayer> players = new array<ref MCPAllPlayer>();
		m_Players.Clear();
		GetGame().GetPlayers(m_Players);

		foreach (Man p : m_Players)
		{
			PlayerIdentity ident = p.GetIdentity();
			if (!ident)
			{
				continue;
			}

			MCPAllPlayer player = new MCPAllPlayer();
			player.uid = ident.GetPlainId();

			vector pos = p.GetPosition();
			player.pos.Insert(pos[0]);
			player.pos.Insert(pos[1]);
			player.pos.Insert(pos[2]);
			player.health = p.GetHealth01("", "");
			player.in_vehicle = p.IsInTransport();
			player.godmode = MCPGodmode.IsOn(p);
			players.Insert(player);
		}

		return players;
	}

	protected MCPPlayerState BuildPlayerState()
	{
		m_Players.Clear();
		GetGame().GetPlayers(m_Players);

		if (m_Players.Count() == 0)
		{
			return null;
		}

		Man player = m_Players.Get(0);
		if (!player)
		{
			return null;
		}

		vector pos = player.GetPosition();
		MCPPlayerState state = new MCPPlayerState();

		PlayerIdentity identity = player.GetIdentity();
		if (identity)
		{
			state.name = identity.GetName();
		}
		else
		{
			state.name = "";
		}

		state.pos.Insert(pos[0]);
		state.pos.Insert(pos[1]);
		state.pos.Insert(pos[2]);
		state.godmode = MCPGodmode.IsOn(player);
		// Water and energy as player_heal reads them (inbox fb98).
		PlayerBase body = PlayerBase.Cast(player);
		if (body)
		{
			MCPPlayerVitals vitals = new MCPPlayerVitals();
			FillPlayerVitals(body, vitals);
			state.water = vitals.water;
			state.energy = vitals.energy;
		}
		return state;
	}

	protected void PostResult(MCPResult result)
	{
		if (!m_Configured || !m_Ctx)
		{
			if (!m_ResultDropLogged)
			{
				m_ResultDropLogged = true;
				Log("result dropped transport unavailable id=" + result.id);
			}
			return;
		}

		JsonSerializer serializer = new JsonSerializer();
		string body;
		bool serialized = serializer.WriteToString(result, false, body);
		if (!serialized)
		{
			Log("result serialize failed id=" + result.id);
			return;
		}

		MCPResultCallback cb = AcquireResultCallback();
		m_CallbackRefs.Insert(cb);
		string resultRequest = "result?key=" + m_Key;
		if (m_PeerInstance != "")
		{
			resultRequest = resultRequest + "&inst=" + EncodeQueryValue(m_PeerInstance);
		}
		m_Ctx.POST(cb, resultRequest, body);
		string okStr = "0";
		if (result.ok) { okStr = "1"; }
		Log("result posted id=" + result.id + " ok=" + okStr + " sent_tick=" + result.tick_poll_sent + " callback_tick=" + result.tick_poll_callback + " dispatch_tick=" + result.tick_dispatch);
	}

	// Only successful requests enter this free-list. Each in-flight POST owns
	// a distinct callback in m_CallbackRefs; pool entries are not admission debt.
	// Existing StartPoll reservation bounds active + idle callbacks to 128.
	protected MCPResultCallback AcquireResultCallback()
	{
		MCPResultCallback cb;
		int last = m_ResultCallbackPool.Count() - 1;
		if (last >= 0)
		{
			cb = m_ResultCallbackPool.Get(last);
			m_ResultCallbackPool.Remove(last);
			cb.AttachBridge(this);
		}
		else
		{
			cb = new MCPResultCallback(this);
		}
		return cb;
	}

	void RecycleResultCallback(MCPResultCallback cb)
	{
		if (m_ResultCallbackPool && m_ResultCallbackPool.Count() < MAX_CALLBACK_REFS)
		{
			m_ResultCallbackPool.Insert(cb);
		}
	}

	void OnResultSuccess(string data, int dataSize)
	{
		Log("result ack size=" + dataSize);
	}

	void OnResultError(int errorCode)
	{
		Log("result post error=" + errorCode);
	}

	void OnResultTimeout()
	{
		Log("result post timeout");
	}

	void ReleaseCallback(RestCallback cb)
	{
		if (!m_CallbackRefs)
		{
			return;
		}

		int idx = m_CallbackRefs.Find(cb);
		if (idx >= 0)
		{
			m_CallbackRefs.Remove(idx);
		}
	}

	void Shutdown()
	{
		MCPBotControl.ShutdownAll(this);
		// A completed cached callback is no longer in m_CallbackRefs.
		if (m_PollCallback)
		{
			m_PollCallback.DetachBridge();
		}
		m_PollCallback = null;

		// A reset may complete outstanding POSTs. Retire their identities first.
		int callbackIndex = 0;
		MCPResultCallback resultCb;
		while (m_CallbackRefs && callbackIndex < m_CallbackRefs.Count())
		{
			resultCb = MCPResultCallback.Cast(m_CallbackRefs.Get(callbackIndex));
			if (resultCb)
			{
				resultCb.DetachBridge();
			}
			callbackIndex = callbackIndex + 1;
		}
		// Idle callbacks already detached in OnSuccess.
		if (m_ResultCallbackPool)
		{
			m_ResultCallbackPool.Clear();
		}
		m_ResultCallbackPool = null;

		if (m_Ctx)
		{
			m_Ctx.reset();
		}

		if (m_CallbackRefs)
		{
			m_CallbackRefs.Clear();
		}

		if (m_Pending)
		{
			m_Pending.Clear();
		}

		if (m_Jobs)
		{
			m_Jobs.Clear();
		}

		if (m_ReadyObjects)
		{
			m_ReadyObjects.Clear();
		}

		if (m_ReadyProxyCargos)
		{
			m_ReadyProxyCargos.Clear();
		}

		if (m_TelemetryFixtureLinePool)
		{
			m_TelemetryFixtureLinePool.Clear();
		}

		m_PollVersion = "";
		m_Ctx = null;
		m_CallbackRefs = null;
		m_Players = null;
		m_Pending = null;
		m_Jobs = null;
		m_ReadyObjects = null;
		m_ReadyProxyCargos = null;
		m_TelemetryFixtureLinePool = null;
		m_Configured = false;
		m_PollInFlight = false;
		m_Accum = 0.0;
		m_Backoff = 0.0;
	}

	static void ShutdownInstance()
	{
		if (m_Instance)
		{
			m_Instance.Shutdown();
			m_Instance = null;
		}
	}

	protected void Log(string message)
	{
		Print("[DayZ-MCP] " + message);
	}
}
