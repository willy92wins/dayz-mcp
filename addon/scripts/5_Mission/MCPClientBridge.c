class MCPClientPollCallback : RestCallback
{
	protected ref MCPClientBridge m_Bridge;

	void MCPClientPollCallback(MCPClientBridge bridge)
	{
		m_Bridge = bridge;
	}

	override void OnSuccess(string data, int dataSize)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnPollSuccess(data, dataSize);
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnPollError(errorCode);
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnPollTimeout();
		}
	}
};

class MCPClientResultCallback : RestCallback
{
	protected ref MCPClientBridge m_Bridge;

	void MCPClientResultCallback(MCPClientBridge bridge)
	{
		m_Bridge = bridge;
	}

	override void OnSuccess(string data, int dataSize)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultSuccess(data, dataSize);
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultError(errorCode);
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultTimeout();
		}
	}
};

class MCPClientBridge extends MCPJobRunnerOwner
{
	protected const int MAX_DISPATCH_PER_TICK = 4;
	protected const int MAX_PENDING = 16;
	protected const int PENDING_POLL_THRESHOLD = 8;
	protected const float CAMERA_JOB_TIMEOUT_S = 5.0;
	protected const float CAMERA_SETTLE_STEP_S = 0.05;
	protected const int CAMERA_DEFAULT_SETTLE_TICKS = 3;
	protected const int CAMERA_MODE_ORIENT = 1;
	protected const int CAMERA_MODE_LOOKAT = 2;
	protected const int CAMERA_MODE_MATRIX = 3;
	protected const int CAMERA_MODE_FREE = 4;
	protected const int CAMERA_PHASE_APPLY = 0;
	protected const int CAMERA_PHASE_SETTLE = 1;
	protected const int CAMERA_PHASE_REPORT = 2;
	protected const float DRIVE_CLIENT_TIMEOUT_S = 12.0;
	protected const float DRIVE_CLIENT_PREP_TIMEOUT_S = 5.0;
	protected const float DRIVE_CLIENT_DEFAULT_SAMPLE_S = 2.0;
	protected const float DRIVE_CLIENT_MAX_SAMPLE_S = 5.0;
	protected const float DRIVE_CLIENT_DEADMAN_S = 3.0;
	protected const float VEHICLE_CONTROL_DEFAULT_TTL_S = 3.0;
	protected const float VEHICLE_CONTROL_MAX_TTL_S = 30.0;
	protected const float DRIVE_CLIENT_SEARCH_RADIUS = 4.0;
	protected const int DRIVE_CLIENT_PHASE_PREP = 0;
	protected const int DRIVE_CLIENT_PHASE_IGNITE = 1;
	protected const int DRIVE_CLIENT_PHASE_DRIVE = 2;
	protected const int DRIVE_CLIENT_PHASE_SAMPLE = 3;
	protected const int DRIVE_CLIENT_PHASE_REPORT = 4;

	protected static ref MCPClientBridge m_Instance;

	protected RestContext m_Ctx;
	protected string m_Url;
	protected string m_Key;
	protected string m_PollVersion;
	protected float m_PollHz;
	protected float m_Accum;
	protected float m_Backoff;
	protected int m_Tick;
	protected int m_TickPollSent;
	protected int m_TickPollCallback;
	protected bool m_PollInFlight;
	protected bool m_Configured;
	protected bool m_InitFailureLogged;
	protected bool m_ControlsSuppressed;
	protected bool m_PlayerSimulationDisabled;
	protected bool m_ActiveCamOwned;
	protected Camera m_ActiveCam;
	protected ref array<ref RestCallback> m_CallbackRefs;
	protected ref array<ref MCPCommand> m_Pending;
	protected ref MCPJobRunner m_JobRunner;
	protected ref array<Object> m_ReadyObjects;
	protected ref array<CargoBase> m_ReadyProxyCargos;

	void MCPClientBridge()
	{
		m_PollHz = 5.0;
		m_Accum = 0.0;
		m_Backoff = 0.0;
		m_Tick = 0;
		m_TickPollSent = 0;
		m_TickPollCallback = 0;
		m_PollVersion = "";
		m_PollInFlight = false;
		m_Configured = false;
		m_InitFailureLogged = false;
		m_ControlsSuppressed = false;
		m_PlayerSimulationDisabled = false;
		m_ActiveCamOwned = false;
		m_CallbackRefs = new array<ref RestCallback>();
		m_Pending = new array<ref MCPCommand>();
		m_JobRunner = new MCPJobRunner();
		m_ReadyObjects = new array<Object>();
		m_ReadyProxyCargos = new array<CargoBase>();
	}

	void ~MCPClientBridge()
	{
		Shutdown();
	}

	static MCPClientBridge Get()
	{
		if (!m_Instance)
		{
			m_Instance = new MCPClientBridge();
		}

		return m_Instance;
	}

	static void ShutdownInstance()
	{
		if (m_Instance)
		{
			m_Instance.Shutdown();
			m_Instance = null;
		}
	}

	void OnTick(float timeslice)
	{
		m_Tick = m_Tick + 1;

		if (m_JobRunner)
		{
			m_JobRunner.Tick(timeslice, this);
		}

		if (!m_Configured)
		{
			TryInit();
			return;
		}

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
		Log("client config loaded path=" + path + " url=" + m_Url + " keylen=" + m_Key.Length() + " poll_hz=" + m_PollHz);
	}

	protected void LogInitFailure(string reason)
	{
		if (m_InitFailureLogged)
		{
			return;
		}

		m_InitFailureLogged = true;
		Log("client init pending: " + reason);
	}

	protected void StartPoll()
	{
		if (!m_Ctx)
		{
			return;
		}

		m_Accum = 0.0;
		m_PollInFlight = true;
		m_TickPollSent = m_Tick;

		MCPClientPollCallback cb = new MCPClientPollCallback(this);
		m_CallbackRefs.Insert(cb);
		string request = "poll?peer=client&key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
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
			Log("client poll parse failed size=" + dataSize + " error=" + parseError);
			OnPollFail("parse_failed");
			return;
		}

		if (!batch.commands)
		{
			Log("client poll returned null commands");
			OnPollFail("null_commands");
			return;
		}

		m_Backoff = 0.0;
		int count = batch.commands.Count();
		if (count > 0)
		{
			Log("client poll commands=" + count + " sent_tick=" + m_TickPollSent + " callback_tick=" + m_TickPollCallback);
		}

		int i = 0;
		while (i < count)
		{
			MCPCommand command = batch.commands.Get(i);
			if (i < MAX_DISPATCH_PER_TICK)
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

		Log("client poll " + reason + " backoff_s=" + m_Backoff);
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
		if (!command || !m_Pending)
		{
			return;
		}

		if (m_Pending.Count() >= MAX_PENDING)
		{
			PostCommandError(command, "client_bridge_queue_full");
			return;
		}

		m_Pending.Insert(command);
	}

	protected bool IsClientInGame()
	{
		return GetGame() && GetGame().GetPlayer();
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

		// Fail-closed readiness gate (BUG-041): every client command touches the
		// local player/camera/vehicle, which the engine has not built during
		// preload. A stale command delivered before the client is in-game crashed
		// the native camera path; reject the whole class before any handler runs.
		if (!IsClientInGame())
		{
			result.ok = false;
			result.error = "client_not_in_game";
			PostResult(result);
			return;
		}

		if (command.cmd == "camera_set")
		{
			postNow = DispatchCameraSet(command, result);
		}
		else if (command.cmd == "camera_get")
		{
			postNow = DispatchCameraGet(command, result);
		}
		else if (command.cmd == "restore_gameplay")
		{
			RestoreGameplay();
			result.ok = true;
		}
		else if (command.cmd == "drive_probe_client")
		{
			postNow = DispatchDriveProbeClient(command, result);
		}
		else if (command.cmd == "vehicle_get_in_client")
		{
			postNow = DispatchVehicleGetInClient(command, result);
		}
		else if (command.cmd == "engine_set")
		{
			postNow = DispatchEngineSet(command, result);
		}
		else if (command.cmd == "vehicle_control")
		{
			postNow = DispatchVehicleControl(command, result);
		}
		else if (command.cmd == "vehicle_telemetry")
		{
			postNow = DispatchVehicleTelemetry(command, result);
		}
		else if (command.cmd == "vehicle_trace")
		{
			postNow = DispatchVehicleTrace(command, result);
		}
		else if (command.cmd == "vehicle_release")
		{
			postNow = DispatchVehicleRelease(command, result);
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

	protected bool DispatchCameraSet(MCPCommand command, MCPResult result)
	{
		MCPCameraValidation validation = ValidateCameraArgs(command.args);
		if (!validation.ok)
		{
			result.ok = false;
			result.error = validation.error;
			return true;
		}

		if (m_JobRunner && m_JobRunner.Count() > 0)
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "camera_set";
		job.args = command.args;
		job.phase = CAMERA_PHASE_APPLY;
		job.sample_s_target = ResolveSettleSeconds(command.args);
		job.deadline_s = m_JobRunner.GetElapsedS() + CAMERA_JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_JobRunner.AddJob(job);

		Log("client job queued id=" + job.id + " kind=camera_set deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool DispatchCameraGet(MCPCommand command, MCPResult result)
	{
		string mode = "get";
		if (command.args && command.args.cam_mode != "")
		{
			mode = command.args.cam_mode;
		}

		result.ok = true;
		result.camera = BuildCameraResult(mode);
		return true;
	}

	protected bool DispatchDriveProbeClient(MCPCommand command, MCPResult result)
	{
		float sampleSTarget = DRIVE_CLIENT_DEFAULT_SAMPLE_S;
		float throttle = 1.0;
		if (command.args)
		{
			if (command.args.throttle < 0.0 || command.args.throttle > 1.0 || !IsFiniteFloat(command.args.throttle))
			{
				result.ok = false;
				result.error = "bad_throttle";
				return true;
			}

			if (command.args.throttle > 0.0)
			{
				throttle = command.args.throttle;
			}

			if (command.args.duration < 0.0 || command.args.duration > DRIVE_CLIENT_MAX_SAMPLE_S || !IsFiniteFloat(command.args.duration))
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

		if (m_JobRunner && m_JobRunner.Count() > 0)
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "drive_probe_client";
		job.args = command.args;
		job.phase = DRIVE_CLIENT_PHASE_PREP;
		job.sample_s_target = sampleSTarget;
		job.deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_TIMEOUT_S;
		job.prep_deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_PREP_TIMEOUT_S;
		job.net_strategy = -1;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_JobRunner.AddJob(job);

		Log("client job queued id=" + job.id + " kind=drive_probe_client deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool DispatchVehicleGetInClient(MCPCommand command, MCPResult result)
	{
		vector seatPos;
		if (!command.args || !ArrayToVector(command.args.pos, seatPos))
		{
			result.ok = false;
			result.error = "no_pos";
			return true;
		}

		if (m_JobRunner && m_JobRunner.Count() > 0)
		{
			result.ok = false;
			result.error = "busy";
			return true;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "vehicle_get_in";
		job.args = command.args;
		job.phase = DRIVE_CLIENT_PHASE_PREP;
		job.deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_TIMEOUT_S;
		job.prep_deadline_s = m_JobRunner.GetElapsedS() + DRIVE_CLIENT_PREP_TIMEOUT_S;
		job.net_strategy = -1;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_JobRunner.AddJob(job);

		Log("client job queued id=" + job.id + " kind=vehicle_get_in deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool DispatchEngineSet(MCPCommand command, MCPResult result)
	{
		string mode = "";
		CarScript car = ResolveOwnedCar();
		if (!car)
		{
			result.ok = false;
			result.error = "not_seated";
			return true;
		}

		if (command.args)
		{
			mode = command.args.mode;
		}

		if (mode == "start")
		{
			car.EngineStart();
		}
		else if (mode == "stop")
		{
			car.EngineStop();
		}
		else
		{
			result.ok = false;
			result.error = "bad_mode";
			return true;
		}

		result.engine_on_server = car.EngineIsOn();
		result.ok = true;
		return true;
	}

	protected bool DispatchVehicleControl(MCPCommand command, MCPResult result)
	{
		MCPArgs args;
		CarScript car;
		float throttle = 0.0;
		float steer = 0.0;
		float brake = 0.0;
		float handbrake = 0.0;
		float ttl = VEHICLE_CONTROL_DEFAULT_TTL_S;
		float holdTtl = 0.0;

		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		args = command.args;
		throttle = args.throttle;
		steer = args.steer;
		brake = args.brake;
		handbrake = args.handbrake;

		if (throttle < 0.0 || throttle > 1.0 || !IsFiniteFloat(throttle))
		{
			result.ok = false;
			result.error = "bad_throttle";
			return true;
		}

		if (steer < -1.0 || steer > 1.0 || !IsFiniteFloat(steer))
		{
			result.ok = false;
			result.error = "bad_steer";
			return true;
		}

		if (brake < 0.0 || brake > 1.0 || !IsFiniteFloat(brake))
		{
			result.ok = false;
			result.error = "bad_brake";
			return true;
		}

		if ((handbrake != 0.0 && handbrake != 1.0) || !IsFiniteFloat(handbrake))
		{
			result.ok = false;
			result.error = "bad_handbrake";
			return true;
		}

		car = ResolveOwnedCar();
		if (!car)
		{
			result.ok = false;
			result.error = "not_seated";
			return true;
		}

		holdTtl = args.hold_ttl_s;
		if (holdTtl > 0.0 && holdTtl <= VEHICLE_CONTROL_MAX_TTL_S && IsFiniteFloat(holdTtl))
		{
			ttl = holdTtl;
		}

		MCPCarDrive.Set(car, throttle, steer, brake, handbrake, GetGame().GetTickTime() + ttl);
		result.engine_on_server = car.EngineIsOn();
		result.ok = true;
		return true;
	}

	protected bool DispatchVehicleTelemetry(MCPCommand command, MCPResult result)
	{
		CarScript car = ResolveOwnedCar();
		if (!car)
		{
			result.ok = false;
			result.error = "not_seated";
			return true;
		}

		result.speedo_max = car.GetSpeedometer();
		result.gear = car.GetGear();
		result.engine_on_server = car.EngineIsOn();
		result.net_strategy = EncodeNetworkMoveStrategy(car.GetNetworkMoveStrategy());
		result.pos_real = new array<float>();
		VectorToArray(car.GetPosition(), result.pos_real);
		result.is_owner = car.IsOwner();
		result.is_authority_owner = car.IsAuthorityOwner();

		PlayerIdentity ownerIdentity = car.GetOwnerIdentity();
		if (ownerIdentity)
		{
			result.owner_identity = ownerIdentity.GetPlainId();
		}
		else
		{
			result.owner_identity = "";
		}

		int lowBits = 0;
		int highBits = 0;
		car.GetNetworkID(lowBits, highBits);
		result.net_id_low = lowBits;
		result.net_id_high = highBits;
		result.ok = true;
		return true;
	}

	protected bool DispatchVehicleTrace(MCPCommand command, MCPResult result)
	{
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		MCPArgs args = command.args;
		if (args.mode != "start" && args.mode != "status" && args.mode != "stop" && args.mode != "read" && args.mode != "clear")
		{
			result.ok = false;
			result.error = "bad_mode";
			return true;
		}
		if (!IsValidTraceId(args.trace_id) || args.cursor < 0 || args.limit < 1 || args.limit > 64 || args.sample_hz < 20 || args.sample_hz > 60 || args.max_samples < 2 || args.max_samples > 8192)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (args.mode == "start")
		{
			PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
			if (!player)
			{
				result.ok = false;
				result.error = "no_player";
				return true;
			}

			HumanCommandVehicle vehicleCommand = player.GetCommand_Vehicle();
			if (!vehicleCommand)
			{
				result.ok = false;
				result.error = "not_seated";
				return true;
			}
			if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)
			{
				result.ok = false;
				result.error = "not_driver";
				return true;
			}

			CarScript car = CarScript.Cast(vehicleCommand.GetTransport());
			if (!car)
			{
				result.ok = false;
				result.error = "no_vehicle";
				return true;
			}
			if (!car.IsOwner())
			{
				result.ok = false;
				result.error = "not_owner";
				return true;
			}
			if (!MCPVehicleTrace.Start(car, args.trace_id, args.sample_hz, args.max_samples))
			{
				result.ok = false;
				result.error = MCPVehicleTrace.GetLastError();
				return true;
			}

			result.trace = MCPVehicleTrace.View("start", args.trace_id, 0, 1);
			result.ok = result.trace != null;
			if (!result.ok)
			{
				result.error = MCPVehicleTrace.GetLastError();
			}
			return true;
		}

		if (!MCPVehicleTrace.Matches(args.trace_id))
		{
			result.ok = false;
			result.error = "trace_not_found";
			return true;
		}

		if (MCPVehicleTrace.IsActive())
		{
			PlayerBase currentPlayer = PlayerBase.Cast(GetGame().GetPlayer());
			HumanCommandVehicle currentCommand;
			CarScript currentCar;
			if (currentPlayer)
			{
				currentCommand = currentPlayer.GetCommand_Vehicle();
			}
			if (currentCommand)
			{
				currentCar = CarScript.Cast(currentCommand.GetTransport());
			}
			if (!currentCommand || currentCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER || currentCar != MCPVehicleTrace.GetCar() || !currentCar.IsOwner())
			{
				MCPVehicleTrace.Fail("driver_changed");
			}
		}

		if (args.mode == "stop")
		{
			if (!MCPVehicleTrace.Stop(args.trace_id))
			{
				result.ok = false;
				result.error = MCPVehicleTrace.GetLastError();
				return true;
			}
			result.trace = MCPVehicleTrace.View("stop", args.trace_id, 0, 1);
		}
		else if (args.mode == "clear")
		{
			MCPVehicleTraceRead clearView = MCPVehicleTrace.View("clear", args.trace_id, 0, 1);
			if (!clearView || !MCPVehicleTrace.Clear(args.trace_id))
			{
				result.ok = false;
				result.error = MCPVehicleTrace.GetLastError();
				return true;
			}
			result.trace = clearView;
		}
		else
		{
			result.trace = MCPVehicleTrace.View(args.mode, args.trace_id, args.cursor, args.limit);
		}

		if (!result.trace)
		{
			result.ok = false;
			result.error = MCPVehicleTrace.GetLastError();
			return true;
		}
		result.ok = true;
		return true;
	}

	protected bool DispatchVehicleRelease(MCPCommand command, MCPResult result)
	{
		MCPVehicleTrace.Abort("vehicle_release");
		MCPCarDrive.Clear();
		result.ok = true;
		return true;
	}

	override bool MCP_ProcessJob(MCPJob job)
	{
		if (!job)
		{
			return false;
		}

		if (job.kind == "camera_set")
		{
			return ProcessCameraSetJob(job);
		}
		else if (job.kind == "drive_probe_client")
		{
			return ProcessDriveProbeClientJob(job);
		}
		else if (job.kind == "vehicle_get_in")
		{
			return ProcessVehicleGetInClientJob(job);
		}

		return false;
	}

	override bool MCP_IsJobReady(MCPJob job)
	{
		return false;
	}

	protected bool ProcessCameraSetJob(MCPJob job)
	{
		if (job.phase == CAMERA_PHASE_APPLY)
		{
			bool applied = ApplyCameraSet(job);
			if (!applied)
			{
				return true;
			}

			job.sample_start_s = m_JobRunner.GetElapsedS();
			job.phase = CAMERA_PHASE_SETTLE;
			return false;
		}

		if (job.phase == CAMERA_PHASE_SETTLE)
		{
			bool interpolationComplete = Camera.IsInterpolationComplete();
			float elapsed = m_JobRunner.GetElapsedS() - job.sample_start_s;
			if (interpolationComplete || elapsed >= job.sample_s_target)
			{
				job.phase = CAMERA_PHASE_REPORT;
				return true;
			}

			return false;
		}

		return true;
	}

	protected bool ProcessDriveProbeClientJob(MCPJob job)
	{
		if (job.phase == DRIVE_CLIENT_PHASE_PREP)
		{
			return ProcessDriveProbeClientPrep(job);
		}
		else if (job.phase == DRIVE_CLIENT_PHASE_IGNITE)
		{
			return ProcessDriveProbeClientIgnite(job);
		}
		else if (job.phase == DRIVE_CLIENT_PHASE_DRIVE)
		{
			return ProcessDriveProbeClientDrive(job);
		}
		else if (job.phase == DRIVE_CLIENT_PHASE_SAMPLE)
		{
			return ProcessDriveProbeClientSample(job);
		}
		else if (job.phase == DRIVE_CLIENT_PHASE_REPORT)
		{
			return true;
		}

		job.error = "bad_probe_phase";
		return true;
	}

	protected Transport FindTransportNearClient(vector pos)
	{
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, DRIVE_CLIENT_SEARCH_RADIUS, m_ReadyObjects, m_ReadyProxyCargos);

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

	protected CarScript ResolveOwnedCar()
	{
		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			return null;
		}

		HumanCommandVehicle vehicleCommand = player.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			return null;
		}

		return CarScript.Cast(vehicleCommand.GetTransport());
	}

	protected bool ProcessVehicleGetInClientJob(MCPJob job)
	{
		if (job.phase == DRIVE_CLIENT_PHASE_PREP)
		{
			return ProcessVehicleGetInClientPrep(job);
		}
		else if (job.phase == DRIVE_CLIENT_PHASE_REPORT)
		{
			return true;
		}

		job.error = "bad_get_in_phase";
		return true;
	}

	protected bool ProcessVehicleGetInClientPrep(MCPJob job)
	{
		PlayerBase player;
		HumanCommandVehicle vehicleCommand;
		vector seatPos;
		Transport foundCar;
		int seatAnim = 0;
		HumanCommandVehicle started;
		CarScript car;

		player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			job.error = "no_player";
			return true;
		}

		if (!job.sim_restored)
		{
			RestoreGameplay();
			job.sim_restored = true;
		}

		vehicleCommand = player.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			if (!job.seat_attempted)
			{
				if (!job.args || !ArrayToVector(job.args.pos, seatPos))
				{
					job.error = "no_pos";
					return true;
				}

				foundCar = FindTransportNearClient(seatPos);
				if (!foundCar)
				{
					job.error = "no_vehicle";
					return true;
				}

				seatAnim = foundCar.GetSeatAnimationType(0);
				started = player.StartCommand_Vehicle(foundCar, 0, seatAnim);
				if (!started)
				{
					job.error = "seat_failed";
					return true;
				}

				started.SetVehicleType(foundCar.GetAnimInstance());
				job.seat_attempted = true;
			}

			if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
			{
				job.error = "not_seated";
				return true;
			}

			return false;
		}

		if (vehicleCommand.IsGettingIn())
		{
			if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
			{
				job.error = "not_seated";
				return true;
			}

			return false;
		}

		if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			job.error = "not_seated";
			return true;
		}

		car = CarScript.Cast(vehicleCommand.GetTransport());
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		job.subject = car;

		if (!job.fixture_attempted)
		{
			if (!IsDriveClientVehicleFixtureReady(car))
			{
				// NOTA: OnDebugSpawn client-side es el conditioning dev (DIAG) del coche de test.
				car.OnDebugSpawn();
			}

			job.fixture_attempted = true;
		}

		if (IsDriveClientVehicleFixtureReady(car))
		{
			job.vehicle_fixture_ready = true;
			CaptureDriveProbeClientOwnership(job, car);
			job.phase = DRIVE_CLIENT_PHASE_REPORT;
			return true;
		}

		if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
		{
			job.vehicle_fixture_ready = false;
			CaptureDriveProbeClientOwnership(job, car);
			job.phase = DRIVE_CLIENT_PHASE_REPORT;
			return true;
		}

		return false;
	}

	protected bool ProcessDriveProbeClientPrep(MCPJob job)
	{
		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			job.error = "no_player";
			return true;
		}

		if (!job.sim_restored)
		{
			RestoreGameplay();
			job.sim_restored = true;
		}

		HumanCommandVehicle vehicleCommand = player.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			if (!job.seat_attempted)
			{
				vector seatPos;
				if (!job.args || !ArrayToVector(job.args.pos, seatPos))
				{
					job.error = "no_pos";
					return true;
				}

				Transport foundCar = FindTransportNearClient(seatPos);
				if (!foundCar)
				{
					job.error = "no_vehicle";
					return true;
				}

				int seatAnim = foundCar.GetSeatAnimationType(0);
				HumanCommandVehicle started = player.StartCommand_Vehicle(foundCar, 0, seatAnim);
				if (!started)
				{
					job.error = "seat_failed";
					return true;
				}

				started.SetVehicleType(foundCar.GetAnimInstance());
				job.seat_attempted = true;
			}

			if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
			{
				job.error = "not_seated";
				return true;
			}

			return false;
		}

		if (vehicleCommand.IsGettingIn())
		{
			if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
			{
				job.error = "not_seated";
				return true;
			}

			return false;
		}

		if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			job.error = "not_driver";
			return true;
		}

		CarScript car = CarScript.Cast(vehicleCommand.GetTransport());
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		job.subject = car;
		CaptureDriveProbeClientOwnership(job, car);

		if (!job.fixture_attempted)
		{
			RestoreGameplay();
			if (job.args && job.args.mode == "suppress")
			{
				SuppressGameplay();
			}

			if (!IsDriveClientVehicleFixtureReady(car))
			{
				// Client-side OnDebugSpawn may be no-op under server authority; S0 also conditions server-side.
				car.OnDebugSpawn();
			}

			job.fixture_attempted = true;
		}

		if (IsDriveClientVehicleFixtureReady(car))
		{
			job.vehicle_fixture_ready = true;
			job.phase = DRIVE_CLIENT_PHASE_IGNITE;
			return false;
		}

		if (m_JobRunner.GetElapsedS() > job.prep_deadline_s)
		{
			job.vehicle_fixture_ready = false;
			job.phase = DRIVE_CLIENT_PHASE_REPORT;
			return true;
		}

		return false;
	}

	protected bool ProcessDriveProbeClientIgnite(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		car.EngineStart();
		job.engine_on_server = car.EngineIsOn();
		job.phase = DRIVE_CLIENT_PHASE_DRIVE;
		return false;
	}

	protected bool ProcessDriveProbeClientDrive(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		float throttle = GetDriveProbeClientThrottle(job);

		MCPCarDrive.Set(car, throttle, 0.0, 0.0, 0.0, GetGame().GetTickTime() + DRIVE_CLIENT_DEADMAN_S);

		job.start_pos = car.GetPosition();
		job.sample_start_s = m_JobRunner.GetElapsedS();
		job.speedo_max = 0.0;
		job.pos_delta = 0.0;
		CaptureDriveProbeClientOwnership(job, car);
		job.phase = DRIVE_CLIENT_PHASE_SAMPLE;
		return false;
	}

	protected bool ProcessDriveProbeClientSample(MCPJob job)
	{
		CarScript car = CarScript.Cast(job.subject);
		if (!car)
		{
			job.error = "no_vehicle";
			return true;
		}

		float throttle = GetDriveProbeClientThrottle(job);
		MCPCarDrive.Set(car, throttle, 0.0, 0.0, 0.0, GetGame().GetTickTime() + DRIVE_CLIENT_DEADMAN_S);

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
		CaptureDriveProbeClientOwnership(job, car);

		if (m_JobRunner.GetElapsedS() - job.sample_start_s >= job.sample_s_target)
		{
			job.phase = DRIVE_CLIENT_PHASE_REPORT;
			return true;
		}

		return false;
	}

	protected float GetDriveProbeClientThrottle(MCPJob job)
	{
		if (job && job.args && job.args.throttle > 0.0)
		{
			return job.args.throttle;
		}

		return 1.0;
	}

	protected void CaptureDriveProbeClientOwnership(MCPJob job, CarScript car)
	{
		if (!job || !car)
		{
			return;
		}

		job.net_strategy = EncodeNetworkMoveStrategy(car.GetNetworkMoveStrategy());
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

	protected bool IsDriveClientVehicleFixtureReady(CarScript car)
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

	protected bool ApplyCameraSet(MCPJob job)
	{
		if (!job || !job.args)
		{
			if (job)
			{
				job.error = "bad_args";
			}
			return false;
		}

		MCPCameraValidation validation = ValidateCameraArgs(job.args);
		if (!validation.ok)
		{
			job.error = validation.error;
			return false;
		}

		SuppressGameplay();

		if (validation.mode_id == CAMERA_MODE_FREE)
		{
			return ApplyFreeCamera(job, validation);
		}

		DeleteOwnedCamera();

		string cameraType = "staticcamera";
		Object cameraObject = g_Game.CreateObject(cameraType, validation.pos, true);
		Camera cam = Camera.Cast(cameraObject);
		if (!cam)
		{
			job.error = "camera_create_failed";
			return false;
		}

		if (validation.mode_id == CAMERA_MODE_MATRIX)
		{
			vector matrix[4];
			if (!ArrayToMatrix(job.args.cam_matrix, matrix))
			{
				job.error = "bad_args";
				g_Game.ObjectDelete(cam);
				return false;
			}
			cam.SetTransform(matrix);
		}
		else
		{
			cam.SetPosition(validation.pos);
			if (validation.mode_id == CAMERA_MODE_LOOKAT)
			{
				cam.LookAt(validation.look_at);
			}
			else
			{
				cam.SetOrientation(validation.orient);
			}
		}

		if (validation.fov > 0.0)
		{
			cam.SetFOV(validation.fov);
		}

		cam.SetActive(true);
		m_ActiveCam = cam;
		m_ActiveCamOwned = true;
		return true;
	}

	protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)
	{
		FreeDebugCamera freeCam = FreeDebugCamera.GetInstance();
		if (!freeCam)
		{
			job.error = "camera_create_failed";
			return false;
		}

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (player)
		{
			player.DisableSimulation(true);
			m_PlayerSimulationDisabled = true;
		}

		freeCam.SetPosition(validation.pos);
		if (job.args.look_at && job.args.look_at.Count() == 3)
		{
			freeCam.LookAt(validation.look_at);
		}
		else
		{
			freeCam.SetOrientation(validation.orient);
		}

		if (validation.fov > 0.0)
		{
			freeCam.SetFOV(validation.fov);
		}

		freeCam.SetActive(true);
		m_ActiveCam = freeCam;
		m_ActiveCamOwned = false;
		return true;
	}

	override void MCP_PostJobSuccess(MCPJob job)
	{
		if (!job)
		{
			return;
		}

		if (job.kind == "camera_set")
		{
			MCPResult result = new MCPResult();
			result.id = job.id;
			result.ok = true;
			result.tick_poll_sent = job.tick_poll_sent;
			result.tick_poll_callback = job.tick_poll_callback;
			result.tick_dispatch = job.tick_dispatch;
			result.camera = BuildCameraResult(job.args.cam_mode);
			PostResult(result);
		}

		if (job.kind == "drive_probe_client")
		{
			MCPResult resultDrive = new MCPResult();
			resultDrive.id = job.id;
			resultDrive.ok = true;
			resultDrive.vehicle_fixture_ready = job.vehicle_fixture_ready;
			resultDrive.engine_on_server = job.engine_on_server;
			resultDrive.speedo_max = job.speedo_max;
			resultDrive.pos_delta = job.pos_delta;
			resultDrive.net_strategy = job.net_strategy;
			resultDrive.is_owner = job.is_owner;
			resultDrive.is_authority_owner = job.is_authority_owner;
			resultDrive.owner_identity = job.owner_identity;
			resultDrive.net_id_low = job.net_id_low;
			resultDrive.net_id_high = job.net_id_high;
			resultDrive.tick_poll_sent = job.tick_poll_sent;
			resultDrive.tick_poll_callback = job.tick_poll_callback;
			resultDrive.tick_dispatch = job.tick_dispatch;
			PostResult(resultDrive);
			MCPCarDrive.Clear();
			RestoreGameplay();
		}

		if (job.kind == "vehicle_get_in")
		{
			MCPResult resultGetIn = new MCPResult();
			resultGetIn.id = job.id;
			resultGetIn.ok = true;
			resultGetIn.seated = true;
			resultGetIn.seat = "driver";
			resultGetIn.vehicle_fixture_ready = job.vehicle_fixture_ready;
			resultGetIn.net_strategy = job.net_strategy;
			resultGetIn.is_owner = job.is_owner;
			resultGetIn.is_authority_owner = job.is_authority_owner;
			resultGetIn.owner_identity = job.owner_identity;
			resultGetIn.net_id_low = job.net_id_low;
			resultGetIn.net_id_high = job.net_id_high;
			resultGetIn.tick_poll_sent = job.tick_poll_sent;
			resultGetIn.tick_poll_callback = job.tick_poll_callback;
			resultGetIn.tick_dispatch = job.tick_dispatch;
			PostResult(resultGetIn);
		}
	}

	override void MCP_PostJobFailure(MCPJob job)
	{
		if (job && job.kind == "drive_probe_client")
		{
			MCPCarDrive.Clear();
			RestoreGameplay();
		}

		if (!job)
		{
			return;
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

	override void MCP_PostJobTimeout(MCPJob job)
	{
		if (job && job.kind == "drive_probe_client")
		{
			MCPCarDrive.Clear();
			RestoreGameplay();
		}

		if (!job)
		{
			return;
		}

		MCPResult result = new MCPResult();
		result.id = job.id;
		result.ok = false;
		result.error = "timeout";
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		PostResult(result);
	}

	override void MCP_ClearJobRefs(MCPJob job)
	{
		if (!job)
		{
			return;
		}

		job.actor = null;
		job.subject = null;
	}

	protected MCPCameraValidation ValidateCameraArgs(MCPArgs args)
	{
		MCPCameraValidation validation = new MCPCameraValidation();
		validation.ok = false;

		if (!args)
		{
			validation.error = "bad_args";
			return validation;
		}

		string mode = args.cam_mode;
		if (mode == "")
		{
			mode = "orient";
			args.cam_mode = mode;
		}

		if (mode == "orient")
		{
			validation.mode_id = CAMERA_MODE_ORIENT;
			if (!ArrayToVector(args.cam_pos, validation.pos) || !ArrayToVector(args.cam_orientation, validation.orient))
			{
				validation.error = "bad_args";
				return validation;
			}
		}
		else if (mode == "lookat")
		{
			validation.mode_id = CAMERA_MODE_LOOKAT;
			if (!ArrayToVector(args.cam_pos, validation.pos) || !ArrayToVector(args.look_at, validation.look_at))
			{
				validation.error = "bad_args";
				return validation;
			}
		}
		else if (mode == "matrix")
		{
			validation.mode_id = CAMERA_MODE_MATRIX;
			if (!ValidateFloatArray(args.cam_matrix, 12))
			{
				validation.error = "bad_args";
				return validation;
			}
			validation.pos = Vector(args.cam_matrix.Get(9), args.cam_matrix.Get(10), args.cam_matrix.Get(11));
		}
		else if (mode == "free")
		{
			validation.mode_id = CAMERA_MODE_FREE;
			if (!ArrayToVector(args.cam_pos, validation.pos))
			{
				validation.error = "bad_args";
				return validation;
			}

			if (args.look_at && args.look_at.Count() == 3)
			{
				if (!ArrayToVector(args.look_at, validation.look_at))
				{
					validation.error = "bad_args";
					return validation;
				}
			}
			else if (!ArrayToVector(args.cam_orientation, validation.orient))
			{
				validation.error = "bad_args";
				return validation;
			}
		}
		else
		{
			validation.error = "bad_args";
			return validation;
		}

		if (args.fov < 0.0 || !IsFiniteFloat(args.fov))
		{
			validation.error = "bad_args";
			return validation;
		}

		validation.fov = args.fov;
		validation.ok = true;
		return validation;
	}

	protected float ResolveSettleSeconds(MCPArgs args)
	{
		int ticks = CAMERA_DEFAULT_SETTLE_TICKS;
		if (args && args.settle_ticks > 0)
		{
			ticks = args.settle_ticks;
		}

		return ticks * CAMERA_SETTLE_STEP_S;
	}

	protected MCPCamera BuildCameraResult(string mode)
	{
		MCPCamera camera = new MCPCamera();
		camera.applied_mode = mode;

		// Defense in depth for the Dispatch readiness gate (BUG-041): the native
		// camera getters deref an unbuilt world camera before the client is
		// in-game and crash. Reached via the camera_set job report too, which
		// does not re-enter Dispatch. pos/matrix/dir stay empty (ctor-initialized).
		if (!IsClientInGame())
		{
			camera.ok = false;
			camera.viewport_moved = false;
			camera.error = "client_not_in_game";
			return camera;
		}

		camera.ok = true;

		Camera current = Camera.GetCurrentCamera();
		if (!current)
		{
			camera.viewport_moved = false;
			camera.error = "player_camera_active";
			VectorToArray(GetGame().GetCurrentCameraPosition(), camera.pos);
			VectorToArray(GetGame().GetCurrentCameraDirection(), camera.dir);
			camera.fov = Camera.GetCurrentFOV();
			camera.interpolation_complete = Camera.IsInterpolationComplete();
			return camera;
		}

		vector matrix[4];
		current.GetTransform(matrix);
		MatrixToArray(matrix, camera.matrix);
		VectorToArray(current.GetWorldPosition(), camera.pos);
		VectorToArray(matrix[2], camera.dir);
		camera.fov = Camera.GetCurrentFOV();
		camera.interpolation_complete = Camera.IsInterpolationComplete();
		camera.viewport_moved = true;
		return camera;
	}

	protected bool ArrayToVector(array<float> values, out vector result)
	{
		if (!ValidateFloatArray(values, 3))
		{
			return false;
		}

		result = Vector(values.Get(0), values.Get(1), values.Get(2));
		return true;
	}

	protected bool ValidateFloatArray(array<float> values, int expectedCount)
	{
		if (!values || values.Count() != expectedCount)
		{
			return false;
		}

		int i = 0;
		while (i < expectedCount)
		{
			float value = values.Get(i);
			if (!IsFiniteFloat(value))
			{
				return false;
			}
			i = i + 1;
		}

		return true;
	}

	protected void VectorToArray(vector v, array<float> a)
	{
		a.Clear();
		a.Insert(v[0]);
		a.Insert(v[1]);
		a.Insert(v[2]);
	}

	protected void MatrixToArray(vector matrix[4], array<float> a)
	{
		a.Clear();
		int row = 0;
		while (row < 4)
		{
			vector v = matrix[row];
			a.Insert(v[0]);
			a.Insert(v[1]);
			a.Insert(v[2]);
			row = row + 1;
		}
	}

	protected bool ArrayToMatrix(array<float> values, out vector matrix[4])
	{
		if (!ValidateFloatArray(values, 12))
		{
			return false;
		}

		int row = 0;
		while (row < 4)
		{
			int offset = row * 3;
			matrix[row] = Vector(values.Get(offset), values.Get(offset + 1), values.Get(offset + 2));
			row = row + 1;
		}

		return true;
	}

	protected bool IsFiniteFloat(float value)
	{
		if (value != value)
		{
			return false;
		}

		return true;
	}

	protected bool IsValidTraceId(string traceId)
	{
		if (traceId.Length() != 32)
		{
			return false;
		}

		string allowed = "0123456789abcdef";
		int index = 0;
		while (index < traceId.Length())
		{
			if (allowed.IndexOf(traceId.Substring(index, 1)) < 0)
			{
				return false;
			}
			index = index + 1;
		}
		return true;
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

	protected void SuppressGameplay()
	{
		if (m_ControlsSuppressed)
		{
			return;
		}

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		MissionGameplay mission = MissionGameplay.Cast(GetGame().GetMission());
		if (!player || !mission)
		{
			return;
		}

		mission.PlayerControlDisable(INPUT_EXCLUDE_ALL);
		if (mission.GetHud())
		{
			mission.GetHud().Show(false);
		}
		m_ControlsSuppressed = true;
	}

	protected void RestoreGameplay()
	{
		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (player && m_PlayerSimulationDisabled)
		{
			player.DisableSimulation(false);
			m_PlayerSimulationDisabled = false;
		}

		MissionGameplay mission = MissionGameplay.Cast(GetGame().GetMission());
		if (!mission)
		{
			return;
		}

		if (m_ControlsSuppressed)
		{
			mission.PlayerControlEnable(true);
			if (mission.GetHud())
			{
				mission.GetHud().Show(true);
			}
			m_ControlsSuppressed = false;
		}
	}

	protected void DeleteOwnedCamera()
	{
		if (m_ActiveCam && m_ActiveCamOwned)
		{
			g_Game.ObjectDelete(m_ActiveCam);
		}
		m_ActiveCam = null;
		m_ActiveCamOwned = false;
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
			Log("client result serialize failed id=" + result.id);
			return;
		}

		MCPClientResultCallback cb = new MCPClientResultCallback(this);
		m_CallbackRefs.Insert(cb);
		m_Ctx.POST(cb, "result?key=" + m_Key, body);
		string okStr = "0";
		if (result.ok)
		{
			okStr = "1";
		}
		Log("client result posted id=" + result.id + " ok=" + okStr + " sent_tick=" + result.tick_poll_sent + " callback_tick=" + result.tick_poll_callback + " dispatch_tick=" + result.tick_dispatch);
	}

	void OnResultSuccess(string data, int dataSize)
	{
		Log("client result ack size=" + dataSize);
	}

	void OnResultError(int errorCode)
	{
		Log("client result post error=" + errorCode);
	}

	void OnResultTimeout()
	{
		Log("client result post timeout");
	}

	void ReleaseCallback(RestCallback cb)
	{
		if (!m_CallbackRefs)
		{
			return;
		}

		int i = 0;
		while (i < m_CallbackRefs.Count())
		{
			if (m_CallbackRefs.Get(i) == cb)
			{
				m_CallbackRefs.Remove(i);
				return;
			}

			i = i + 1;
		}
	}

	void Shutdown()
	{
		MCPVehicleTrace.Abort("shutdown");
		MCPCarDrive.Clear();
		RestoreGameplay();
		if (m_ActiveCam)
		{
			m_ActiveCam.SetActive(false);
		}
		DeleteOwnedCamera();

		if (m_Ctx)
		{
			m_Ctx.reset();
			m_Ctx = null;
		}

		if (m_CallbackRefs)
		{
			m_CallbackRefs.Clear();
		}

		if (m_Pending)
		{
			m_Pending.Clear();
		}

		if (m_JobRunner)
		{
			m_JobRunner.Clear();
		}

		m_Configured = false;
		m_PollInFlight = false;
		m_PollVersion = "";
		m_Accum = 0.0;
		m_Backoff = 0.0;
	}

	protected void Log(string message)
	{
		Print("[MCP-CLIENT] " + message);
	}
};
