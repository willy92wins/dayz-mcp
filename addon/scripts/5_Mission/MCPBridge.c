class MCPBridge
{
	protected const int MAX_DISPATCH_PER_TICK = 4;
	protected const int MAX_PENDING = 32;
	protected const int PENDING_POLL_THRESHOLD = 8;
	protected const float JOB_TIMEOUT_S = 5.0;
	protected const float DRIVE_PROBE_TIMEOUT_S = 12.0;
	protected const float DRIVE_PROBE_PREP_TIMEOUT_S = 5.0;
	protected const float DRIVE_PROBE_DEFAULT_SAMPLE_S = 2.0;
	protected const float DRIVE_PROBE_MAX_SAMPLE_S = 5.0;
	protected const int VEHICLE_SEARCH_RADIUS = 4;
	// 8 m covers a truck-length hull around the subject's origin after ECE_TRACE snap.
	protected const float SPAWN_READY_RADIUS = 8.0;
	protected const int TELEMETRY_ITEMS_CAP = 16;
	protected const int TELEMETRY_JSONL_DEFAULT_MAX_LINES = 64;
	protected const int MAX_JSONL_LINE_CHARS = 4096;
	protected const float RAYCAST_MAX_RADIUS = 5.0;
	protected const float TELEMETRY_OBJECT_AT_MAX_RADIUS = 50.0;
	// F3.4 / F3.6: fixed lookup radius when type+pos resolve an in-world object.
	protected const float OBJECT_LOOKUP_RADIUS = 25.0;
	protected const int DRIVE_PROBE_PHASE_PREP = 0;
	protected const int DRIVE_PROBE_PHASE_IGNITE = 1;
	protected const int DRIVE_PROBE_PHASE_DRIVE = 2;
	protected const int DRIVE_PROBE_PHASE_SAMPLE = 3;
	protected const int DRIVE_PROBE_PHASE_REPORT = 4;

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
	// disk is re-read (BUG-071).
	protected const float KEY_RELOAD_BACKOFF_S = 4.0;
	protected float m_Backoff;
	protected int m_Tick;
	protected int m_TickPollSent;
	protected int m_TickPollCallback;
	protected bool m_PollInFlight;
	protected bool m_Configured;
	protected bool m_InitFailureLogged;
	protected ref array<ref RestCallback> m_CallbackRefs;
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
		m_CallbackRefs = new array<ref RestCallback>();
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

		string loopbackPrefix = "http://127.0.0.1:";
		if (!StringHasPrefix(cfg.url, loopbackPrefix))
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
		m_Accum = 0.0;
		m_PollInFlight = true;
		m_TickPollSent = m_Tick;

		MCPPollCallback cb = new MCPPollCallback(this);
		m_CallbackRefs.Insert(cb);
		string request = "poll?key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
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

	void OnPollError(int errorCode)
	{
		OnPollFail("error=" + errorCode);
	}

	void OnPollTimeout()
	{
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

	// BUG-071: the key is read once at configure time and never again, so rotating
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
		else if (command.cmd == "vehicle_drive")
		{
			postNow = DispatchVehicleDriveProbe(command, result);
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
		else if (command.cmd == "object_anim")
		{
			postNow = DispatchObjectAnim(command, result);
		}
		else if (command.cmd == "inventory_give")
		{
			postNow = DispatchInventoryGive(command, result);
		}
		else if (command.cmd == "object_inspect")
		{
			postNow = DispatchObjectInspect(command, result);
		}
		else if (command.cmd == "world_time_set")
		{
			postNow = DispatchWorldTimeSet(command, result);
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

	protected bool DispatchVehicleDriveProbe(MCPCommand command, MCPResult result)
	{
		float sampleSTarget = DRIVE_PROBE_DEFAULT_SAMPLE_S;
		float throttle = 1.0;
		if (command.args)
		{
			if (command.args.throttle < 0.0 || command.args.throttle > 1.0)
			{
				result.ok = false;
				result.error = "bad_throttle";
				return true;
			}

			if (command.args.throttle > 0.0)
			{
				throttle = command.args.throttle;
			}

			if (command.args.duration < 0.0 || command.args.duration > DRIVE_PROBE_MAX_SAMPLE_S)
			{
				result.ok = false;
				result.error = "bad_duration";
				return true;
			}

			if (command.args.duration > 0.0)
			{
				sampleSTarget = command.args.duration;
			}

			command.args.throttle = throttle;
		}

		Human human = GetFirstHuman();
		Object subject = null;
		if (human)
		{
			HumanCommandVehicle vehicleCommand = human.GetCommand_Vehicle();
			if (vehicleCommand)
			{
				subject = vehicleCommand.GetTransport();
			}
		}

		if (HasActiveJobFor(human, subject))
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "drive_probe";
		job.args = command.args;
		job.actor = human;
		job.subject = subject;
		job.deadline_s = m_ElapsedS + DRIVE_PROBE_TIMEOUT_S;
		job.prep_deadline_s = m_ElapsedS + DRIVE_PROBE_PREP_TIMEOUT_S;
		job.phase = DRIVE_PROBE_PHASE_PREP;
		job.sample_s_target = sampleSTarget;
		job.net_strategy = -1;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Jobs.Insert(job.id, job);

		Log("job queued id=" + job.id + " kind=drive_probe deadline_s=" + job.deadline_s);
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

		vector applied;
		if (veh)
		{
			vector mat[4];
			veh.GetTransform(mat);
			mat[3] = position;
			veh.SetTransform(mat);
			// The occupant's own position lags the transport until the next sim frame;
			// the transport is what moved, so it is what pos_real reports.
			applied = veh.GetPosition();
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

	// F3.4: read GetAnimationPhase or write SetAnimationPhase on Entity (entity.c:12-15).
	// phase == MCP_ARG_FLOAT_UNSET means read-only.
	protected bool DispatchObjectAnim(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.type == "" || command.args.source == "" || !command.args.pos || command.args.pos.Count() != 3)
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

		bool writePhase = command.args.phase != MCP_ARG_FLOAT_UNSET;
		if (writePhase && !IsFiniteFloat(command.args.phase))
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

		Entity entity = Entity.Cast(match);
		if (!entity)
		{
			result.ok = false;
			result.error = "not_entity";
			return true;
		}

		if (writePhase)
		{
			entity.SetAnimationPhase(command.args.source, command.args.phase);
		}

		result.phase = entity.GetAnimationPhase(command.args.source);
		result.source = command.args.source;
		result.type = command.args.type;
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

		result.classname = command.args.classname;
		result.type = spawned.GetType();
		result.found = true;
		result.deferred = false;
		result.ok = true;
		return true;
	}

	// Raw nearby objects via GetObjectsAtPosition3D. No classname filter.
	// result.entities is the nearest `limit` hits; result.count_total is the uncut size.
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

	// F3.6: memory points and bounding center. Missing memory points are product FAIL
	// (exists:false) with ok:true — never a tool error.
	protected bool DispatchObjectInspect(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.type == "" || !command.args.pos || command.args.pos.Count() != 3 || !command.args.want || command.args.want.Count() == 0)
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

		string error = "";
		Object match = FindUniqueObjectNearType(command.args.type, Vector(px, py, pz), OBJECT_LOOKUP_RADIUS, error);
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
			if (wantName == "bounding_center")
			{
				vector center = match.GetBoundingCenter();
				VectorToArray(center, inspect.bounding_center);
				inspect.has_bounding_center = true;
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
		result.ok = true;
		return true;
	}

	// Resolve a single world object by classname near pos. Zero matches -> object_not_found;
	// more than one -> ambiguous_object. Used by object_anim and object_inspect.
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
		if (command.args.time_multiplier != MCP_ARG_FLOAT_UNSET)
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

		if (command.args.overcast != MCP_ARG_FLOAT_UNSET)
		{
			overcast.Set(command.args.overcast, changeTime, minDuration);
		}
		if (command.args.rain != MCP_ARG_FLOAT_UNSET)
		{
			rain.Set(command.args.rain, changeTime, minDuration);
		}
		if (command.args.fog != MCP_ARG_FLOAT_UNSET)
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
		if (args.time_multiplier != MCP_ARG_FLOAT_UNSET)
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
		if (args.overcast != MCP_ARG_FLOAT_UNSET)
		{
			hasPhenomenon = true;
			if (!ValidateWeatherValue(args.overcast))
			{
				result.ok = false;
				result.error = "bad_overcast";
				return false;
			}
		}
		if (args.rain != MCP_ARG_FLOAT_UNSET)
		{
			hasPhenomenon = true;
			if (!ValidateWeatherValue(args.rain))
			{
				result.ok = false;
				result.error = "bad_rain";
				return false;
			}
		}
		if (args.fog != MCP_ARG_FLOAT_UNSET)
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

		// BUG-066(c): the attachment-slot family, not the belongs-to family.
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

		validation.pos = Vector(x, y, z);
		validation.ok = true;
		return validation;
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
				if (IsJobReady(job))
				{
					PostJobSuccess(job);
					job.actor = null;
					job.subject = null;
					m_Jobs.Remove(jobId);
				}
				else if (job.kind == "drive_probe" && ProcessDriveProbe(job))
				{
					if (job.error != "")
					{
						PostJobFailure(job);
					}
					else
					{
						PostJobSuccess(job);
					}
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

	protected bool ProcessDriveProbe(MCPJob job)
	{
		if (job.phase == DRIVE_PROBE_PHASE_PREP)
		{
			return ProcessDriveProbePrep(job);
		}
		else if (job.phase == DRIVE_PROBE_PHASE_IGNITE)
		{
			return ProcessDriveProbeIgnite(job);
		}
		else if (job.phase == DRIVE_PROBE_PHASE_DRIVE)
		{
			return ProcessDriveProbeDrive(job);
		}
		else if (job.phase == DRIVE_PROBE_PHASE_SAMPLE)
		{
			return ProcessDriveProbeSample(job);
		}
		else if (job.phase == DRIVE_PROBE_PHASE_REPORT)
		{
			return true;
		}

		job.error = "bad_probe_phase";
		return true;
	}

	protected bool ProcessDriveProbePrep(MCPJob job)
	{
		Human human = job.actor;
		if (!human)
		{
			human = GetFirstHuman();
			job.actor = human;
		}

		if (!human)
		{
			job.error = "not_seated";
			return true;
		}

		HumanCommandVehicle vehicleCommand = human.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			job.error = "not_seated";
			return true;
		}

		CarScript car = CarScript.Cast(vehicleCommand.GetTransport());
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		job.subject = car;

		if (!job.fixture_attempted)
		{
			if (!IsVehicleFixtureReady(car))
			{
				car.OnDebugSpawn();
			}

			job.fixture_attempted = true;
		}

		if (IsVehicleFixtureReady(car))
		{
			job.vehicle_fixture_ready = true;
			job.phase = DRIVE_PROBE_PHASE_IGNITE;
			return false;
		}

		if (m_ElapsedS > job.prep_deadline_s)
		{
			job.vehicle_fixture_ready = false;
			job.phase = DRIVE_PROBE_PHASE_REPORT;
			return true;
		}

		return false;
	}

	protected bool ProcessDriveProbeIgnite(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		car.EngineStart();
		job.engine_on_server = car.EngineIsOn();
		job.phase = DRIVE_PROBE_PHASE_DRIVE;
		return false;
	}

	protected bool ProcessDriveProbeDrive(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		float throttle = GetDriveProbeThrottle(job);

		car.SetHandbrake(0);
		car.SetBrake(0);
		if (car.GetGear() < CarGear.FIRST)
		{
			car.ShiftTo(CarGear.FIRST);
		}
		car.SetThrottle(throttle);

		job.start_pos = car.GetPosition();
		job.sample_start_s = m_ElapsedS;
		job.speedo_max = 0.0;
		job.pos_delta = 0.0;
		job.net_strategy = EncodeNetworkMoveStrategy(car.GetNetworkMoveStrategy());
		CaptureDriveProbeOwnership(job, car);
		job.phase = DRIVE_PROBE_PHASE_SAMPLE;
		return false;
	}

	protected bool ProcessDriveProbeSample(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		float throttle = GetDriveProbeThrottle(job);
		car.SetThrottle(throttle);
		car.SetHandbrake(0);
		car.SetBrake(0);
		if (car.GetGear() < CarGear.FIRST)
		{
			car.ShiftTo(CarGear.FIRST);
		}

		if (car.EngineIsOn())
		{
			job.engine_on_server = true;
		}

		float speed = car.GetSpeedometer();
		if (speed > job.speedo_max)
		{
			job.speedo_max = speed;
		}

		vector delta = car.GetPosition() - job.start_pos;
		job.pos_delta = delta.Length();
		job.net_strategy = EncodeNetworkMoveStrategy(car.GetNetworkMoveStrategy());
		CaptureDriveProbeOwnership(job, car);

		if (m_ElapsedS - job.sample_start_s >= job.sample_s_target)
		{
			job.phase = DRIVE_PROBE_PHASE_REPORT;
			return true;
		}

		return false;
	}

	protected void CaptureDriveProbeOwnership(MCPJob job, CarScript car)
	{
		if (!job || !car)
		{
			return;
		}

		job.is_owner = car.IsOwner();
		job.is_authority_owner = car.IsAuthorityOwner();

		PlayerIdentity ownerIdentity = car.GetOwnerIdentity();
		if (ownerIdentity)
		{
			job.owner_identity = ownerIdentity.GetPlainId();
		}
		else
		{
			job.owner_identity = "";
		}

		int lowBits = 0;
		int highBits = 0;
		car.GetNetworkID(lowBits, highBits);
		job.net_id_low = lowBits;
		job.net_id_high = highBits;
	}

	protected float GetDriveProbeThrottle(MCPJob job)
	{
		if (job && job.args && job.args.throttle > 0.0)
		{
			return job.args.throttle;
		}

		return 1.0;
	}

	protected int EncodeNetworkMoveStrategy(NetworkMoveStrategy strategy)
	{
		if (strategy == NetworkMoveStrategy.NONE)
		{
			return 0;
		}

		if (strategy == NetworkMoveStrategy.LATEST)
		{
			return 1;
		}

		if (strategy == NetworkMoveStrategy.PHYSICS)
		{
			return 2;
		}

		return -1;
	}

	protected void PostJobSuccess(MCPJob job)
	{
		if (job.kind == "seat")
		{
			PostSeatSuccess(job);
			return;
		}

		if (job.kind == "drive_probe")
		{
			PostDriveProbeResult(job);
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

	protected void PostDriveProbeResult(MCPJob job)
	{
		// MCP-PROBE B3 server-side -- DECISION DATA, no es la tool final
		MCPResult result = new MCPResult();
		result.id = job.id;
		result.ok = true;
		result.vehicle_fixture_ready = job.vehicle_fixture_ready;
		result.engine_on_server = job.engine_on_server;
		result.speedo_max = job.speedo_max;
		result.pos_delta = job.pos_delta;
		result.net_strategy = job.net_strategy;
		result.is_owner = job.is_owner;
		result.is_authority_owner = job.is_authority_owner;
		result.owner_identity = job.owner_identity;
		result.net_id_low = job.net_id_low;
		result.net_id_high = job.net_id_high;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		PostResult(result);
	}

	protected void NeutralizeDriveProbeControls(MCPJob job)
	{
		if (!job)
		{
			return;
		}

		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			return;
		}

		car.SetThrottle(0);
		car.SetBrake(0);
		car.SetHandbrake(0);
	}

	protected void PostJobFailure(MCPJob job)
	{
		if (job.kind == "drive_probe")
		{
			NeutralizeDriveProbeControls(job);
		}

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
		if (job.kind == "drive_probe")
		{
			NeutralizeDriveProbeControls(job);
		}

		MCPResult result = new MCPResult();
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
		return state;
	}

	protected void PostResult(MCPResult result)
	{
		if (!m_Configured || !m_Ctx)
		{
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

		MCPResultCallback cb = new MCPResultCallback(this);
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
		Print("[MCP-POC] " + message);
	}
}
