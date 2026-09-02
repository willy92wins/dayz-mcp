class MCPClientPollCallback : RestCallback
{
	protected ref MCPClientBridge m_Bridge;

	void MCPClientPollCallback(MCPClientBridge bridge)
	{
		m_Bridge = bridge;
	}

	void DetachBridge()
	{
		m_Bridge = null;
	}

	override void OnSuccess(string data, int dataSize)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
			m_Bridge.OnPollSuccess(data, dataSize);
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
			m_Bridge.OnPollError(errorCode);
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				return;
			}
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

	void DetachBridge()
	{
		m_Bridge = null;
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

class MCPClientDialogSink : MCPDialogSink
{
	protected MCPClientBridge m_Owner;

	void MCPClientDialogSink(MCPClientBridge owner)
	{
		m_Owner = owner;
	}

	override void OnDialogResult(MCPDialogResult dialog)
	{
		if (m_Owner)
		{
			m_Owner.AcceptDialogResult(dialog);
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
	protected const int UI_TREE_DEFAULT_LIMIT = 256;
	protected const int UI_TREE_MAX_LIMIT = 512;
	protected const float ACTION_USE_DEFAULT_RADIUS = 5.0;

	protected static ref MCPClientBridge m_Instance;

	protected RestContext m_Ctx;
	protected RestContext m_PollCtx;
	protected string m_Url;
	protected string m_Key;
	protected string m_PeerInstance;
	protected string m_PollVersion;
	protected float m_PollHz;
	protected float m_Accum;
	// Backoff at which a poll failure stops looking transient and the credential on
	// disk is re-read.
	protected const float KEY_RELOAD_BACKOFF_S = 4.0;
	protected const float POLL_WATCHDOG_S = 30.0;
	protected float m_Backoff;
	protected int m_Tick;
	protected int m_TickPollSent;
	protected int m_TickPollCallback;
	protected bool m_PollInFlight;
	protected float m_PollInFlightS;
	protected bool m_Configured;
	protected bool m_InitFailureLogged;
	protected bool m_ControlsSuppressed;
	protected bool m_PlayerSimulationDisabled;
	protected bool m_ActiveCamOwned;
	protected Camera m_ActiveCam;
	protected ref array<ref RestCallback> m_CallbackRefs;
	protected ref array<ref RestCallback> m_PollCallbackRefs;
	protected ref MCPClientPollCallback m_PollCallback;
	protected ref array<ref MCPCommand> m_Pending;
	protected ref MCPJobRunner m_JobRunner;
	protected ref array<Object> m_ReadyObjects;
	protected ref array<CargoBase> m_ReadyProxyCargos;
	protected ref MCPDialogController m_Dialog;
	protected ref MCPDialogSink m_DialogSink;
	protected MCPJob m_DialogJob;
	protected bool m_DialogHostTried;
	//! Hot-UI preview root. Not ref: the workspace owns the widget tree; this
	//! pointer only exists so the next reload can Unlink the previous one.
	protected Widget m_UiPreviewRoot;

	void MCPClientBridge()
	{
		m_PollHz = 5.0;
		m_Accum = 0.0;
		m_Backoff = 0.0;
		m_Tick = 0;
		m_TickPollSent = 0;
		m_TickPollCallback = 0;
		m_PollVersion = "";
		m_PeerInstance = "";
		m_PollInFlight = false;
		m_PollInFlightS = 0.0;
		m_Configured = false;
		m_InitFailureLogged = false;
		m_ControlsSuppressed = false;
		m_PlayerSimulationDisabled = false;
		m_ActiveCamOwned = false;
		m_CallbackRefs = new array<ref RestCallback>();
		m_PollCallbackRefs = new array<ref RestCallback>();
		m_Pending = new array<ref MCPCommand>();
		m_JobRunner = new MCPJobRunner();
		m_ReadyObjects = new array<Object>();
		m_ReadyProxyCargos = new array<CargoBase>();
		m_DialogHostTried = false;
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

		if (!m_DialogHostTried && IsClientInGame())
		{
			m_DialogHostTried = true;
			EnsureDialogHost();
		}

		DrainPending();

		if (m_PollInFlight)
		{
			m_PollInFlightS = m_PollInFlightS + timeslice;
			if (m_PollInFlightS >= POLL_WATCHDOG_S)
			{
				int pollRefs = 0;
				if (m_PollCallbackRefs)
				{
					pollRefs = m_PollCallbackRefs.Count();
				}
				Log("client poll watchdog no callback in_flight_s=" + m_PollInFlightS + " poll_refs=" + pollRefs);
				AbandonInFlightPoll();
				OnPollFail("watchdog");
			}
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
		m_PollCtx = api.GetRestContext(PollContextUrl(m_Url));
		if (!m_Ctx || !m_PollCtx)
		{
			LogInitFailure("RestContext unavailable");
			return;
		}

		m_Ctx.SetHeader("application/json");
		m_PollCtx.SetHeader("application/json");
		m_Configured = true;
		m_Backoff = 0.0;
		m_Accum = 0.0;
		Log("client config loaded path=" + path + " url=" + m_Url + " keylen=" + m_Key.Length() + " instlen=" + m_PeerInstance.Length() + " poll_hz=" + m_PollHz);
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
		if (!m_PollCtx)
		{
			return;
		}

		m_Accum = 0.0;
		m_PollInFlight = true;
		m_PollInFlightS = 0.0;
		m_TickPollSent = m_Tick;

		EnsurePollCallback();
		HoldPollCallback();
		string request = "poll?peer=client&key=" + m_Key;
		request = request + "&ver=" + GetPollVersion();
		if (m_PeerInstance != "")
		{
			request = request + "&inst=" + EncodeQueryValue(m_PeerInstance);
		}
		m_PollCtx.GET(m_PollCallback, request);
	}

	protected void EnsurePollCallback()
	{
		if (!m_PollCallback)
		{
			m_PollCallback = new MCPClientPollCallback(this);
		}
	}

	protected void HoldPollCallback()
	{
		if (!m_PollCallbackRefs || !m_PollCallback)
		{
			return;
		}

		int i = 0;
		while (i < m_PollCallbackRefs.Count())
		{
			if (m_PollCallbackRefs.Get(i) == m_PollCallback)
			{
				return;
			}

			i = i + 1;
		}

		m_PollCallbackRefs.Insert(m_PollCallback);
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

	bool IsActivePollCallback(MCPClientPollCallback cb)
	{
		return cb == m_PollCallback;
	}

	// Poll shares the results RestContext (same URL). GetRestContext caches by
	// exact string, so a distinct poll base is unreachable, and stripping the
	// trailing slash makes the engine concatenate base+request into a malformed
	// URL (error=7). Isolation of stale polls is identity discard of the
	// abandoned callback, not a second context. AbandonInFlightPoll must not
	// reset() when m_PollCtx == m_Ctx.
	protected string PollContextUrl(string url)
	{
		return url;
	}

	// reset() only a distinct poll context (restapi.c:133). Shared results
	// context must not reset() here (would cancel in-flight POSTs). Detach the
	// live pointer so the next StartPoll allocates a new callback; keep the
	// abandoned object in m_PollCallbackRefs until its GET completion calls
	// ReleaseCallback. Engine retain of RestCallback passed to GET is unknown,
	// so script-side hold is required either way. An orphan that never
	// completes stays retained (bounded residual leak: one object per abandon).
	protected void AbandonInFlightPoll()
	{
		m_PollCallback = null;

		if (m_PollCtx && m_PollCtx != m_Ctx)
		{
			m_PollCtx.reset();
			m_PollCtx.SetHeader("application/json");
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
		m_PollInFlightS = 0.0;
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
		Log("client poll key reloaded path=" + path + " keylen=" + m_Key.Length());
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

	protected bool HasExclusiveJob()
	{
		if (!m_JobRunner)
		{
			return false;
		}

		return m_JobRunner.CountExcluding("ui_dialog") > 0;
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

		// Fail-closed readiness gate: every client command touches the
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
			ReleaseCamera();
			result.ok = true;
		}
		else if (command.cmd == "key_press")
		{
			postNow = DispatchKeyPress(command, result);
		}
		else if (command.cmd == "player_respawn")
		{
			postNow = DispatchPlayerRespawn(command, result);
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
		else if (command.cmd == "ui_tree")
		{
			postNow = DispatchUiTree(command, result);
		}
		else if (command.cmd == "ui_set_text")
		{
			postNow = DispatchUiSetText(command, result);
		}
		else if (command.cmd == "ui_click")
		{
			postNow = DispatchUiClick(command, result);
		}
		else if (command.cmd == "ui_reload_layout")
		{
			postNow = DispatchUiReloadLayout(command, result);
		}
		else if (command.cmd == "ui_focus")
		{
			postNow = DispatchUiFocus(command, result);
		}
		else if (command.cmd == "action_use")
		{
			postNow = DispatchActionUse(command, result);
		}
		else if (command.cmd == "ui_dialog")
		{
			postNow = DispatchUiDialog(command, result);
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

	protected bool DispatchKeyPress(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.dik < 0)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (!GetGame() || !GetGame().GetMission())
		{
			result.ok = false;
			result.error = "no_mission";
			return true;
		}

		int dik = command.args.dik;
		GetGame().GetMission().OnKeyPress(dik);
		result.delivered = true;
		result.dik = dik;
		result.ok = true;
		return true;
	}

	protected bool DispatchPlayerRespawn(MCPCommand command, MCPResult result)
	{
		if (!GetGame())
		{
			result.ok = false;
			result.error = "no_game";
			return true;
		}

		MissionGameplay missionGP = MissionGameplay.Cast(GetGame().GetMission());
		if (!missionGP)
		{
			result.ok = false;
			result.error = "no_mission";
			return true;
		}

		UIScriptedMenu respawnMenu;
		UIManager ui = GetGame().GetUIManager();
		if (ui)
		{
			respawnMenu = ui.GetMenu();
		}

		// Mirror vanilla InGameMenu.GameRespawn (5_Mission/gui/ingamemenu.c).
		GetGame().GetMenuDefaultCharacterData(false).SetRandomCharacterForced(true);
		GetGame().RespawnPlayer();

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (player)
		{
			player.SimulateDeath(true);
			GetGame().GetCallQueue(CALL_CATEGORY_GUI).Call(player.ShowDeadScreen, true, 0);
		}

		missionGP.DestroyAllMenus();
		missionGP.SetPlayerRespawning(true);
		missionGP.Continue();
		if (respawnMenu)
		{
			respawnMenu.Close();
		}

		result.requested = true;
		result.ok = true;
		return true;
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

		if (HasExclusiveJob())
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

		if (HasExclusiveJob())
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

		if (command.args.seat < 0 || command.args.seat > 63)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (HasExclusiveJob())
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

	protected bool DispatchUiTree(MCPCommand command, MCPResult result)
	{
		MCPArgs args = command.args;
		string error = "";
		Widget root = ResolveUiRoot(args, error);
		if (!root)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		int limit = UI_TREE_DEFAULT_LIMIT;
		if (args && args.limit > 0)
		{
			limit = args.limit;
		}
		if (limit > UI_TREE_MAX_LIMIT)
		{
			limit = UI_TREE_MAX_LIMIT;
		}

		MCPUiSnapshot snap = new MCPUiSnapshot();
		CollectUiNodes(root, snap, limit);
		result.ui = snap;
		result.ok = true;
		return true;
	}

	protected bool DispatchUiSetText(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.path == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Widget target = ResolveUiRoot(command.args, error);
		if (!target)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		string text = command.args.text;
		EditBoxWidget editBox = EditBoxWidget.Cast(target);
		if (editBox)
		{
			editBox.SetText(text);
		}
		else
		{
			MultilineEditBoxWidget multi = MultilineEditBoxWidget.Cast(target);
			if (multi)
			{
				multi.SetText(text);
			}
			else
			{
				ButtonWidget btn = ButtonWidget.Cast(target);
				if (btn)
				{
					btn.SetText(text);
				}
				else
				{
					// Checked last on purpose: the widgets above may derive from
					// TextWidget, and Cast would claim them first. SetText exists
					// (1_core\proto\enwidgets.c:195) but GetText does not, so this
					// write is not readable back through ui_tree.
					TextWidget label = TextWidget.Cast(target);
					if (label)
					{
						label.SetText(text);
					}
					else
					{
						result.ok = false;
						result.error = "text_not_writable";
						return true;
					}
				}
			}
		}

		MCPUiSnapshot snap = new MCPUiSnapshot();
		MCPUiNode node = new MCPUiNode();
		FillUiNode(target, node);
		snap.nodes.Insert(node);
		result.ui = snap;
		result.ok = true;
		return true;
	}

	protected bool DispatchUiClick(MCPCommand command, MCPResult result)
	{
		if (!command.args || command.args.path == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		int mouseButton = command.args.button;
		if (mouseButton < 0 || mouseButton > 2)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		string error = "";
		Widget target = ResolveUiRoot(command.args, error);
		if (!target)
		{
			result.ok = false;
			result.error = error;
			return true;
		}

		string handlerName = "";
		bool didClick = InvokeUiClick(target, mouseButton, handlerName);
		result.user_id = target.GetUserID();
		result.handler = handlerName;
		result.clicked = didClick;
		if (!didClick)
		{
			result.ok = false;
			//! Empty name = the walk found no handler at all. A named handler that
			//! returned false ran and declined. Collapsing both codes would leave the
			//! verb unable to report a click that reached a handler and did nothing.
			if (handlerName == "")
			{
				result.error = "no_handler";
			}
			else
			{
				result.error = "not_handled";
			}
			return true;
		}

		result.ok = true;
		return true;
	}

	//! Hot UI iteration: rebuild a standalone preview root from a .layout on disk
	//! and report the engine rects of the resulting tree. $profile: is the only
	//! prefix the engine re-reads without a repack; an addon-prefixed path is
	//! served by the PBO and never sees a loose file (measured 2026-08-19).
	//! mode="close" unlinks the preview and loads nothing.
	protected bool DispatchUiReloadLayout(MCPCommand command, MCPResult result)
	{
		MCPArgs args = command.args;
		if (!args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		bool closing = (args.mode == "close");
		if (!closing && args.path == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (!GetGame())
		{
			result.ok = false;
			result.error = "no_game";
			return true;
		}

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		if (!workspace)
		{
			result.ok = false;
			result.error = "no_workspace";
			return true;
		}

		// Unlink first, always: CreateWidgets stacks a second root on top of the
		// first, and the returned snapshot would stop describing what is on screen.
		if (m_UiPreviewRoot)
		{
			m_UiPreviewRoot.Unlink();
			m_UiPreviewRoot = null;
		}

		MCPUiSnapshot snap = new MCPUiSnapshot();
		if (closing)
		{
			result.ui = snap;
			result.source = "";
			result.ok = true;
			Log("ui_reload_layout closed preview");
			return true;
		}

		// FileExist (1_core\proto\ensystem.c:397) resolves VFS paths and is the only
		// guard between here and a CTD: CreateWidgets on a missing layout dies inside
		// the native call, taking the client with it.
		if (!FileExist(args.path))
		{
			result.ok = false;
			result.error = "layout_not_found";
			return true;
		}

		m_UiPreviewRoot = workspace.CreateWidgets(args.path);
		if (!m_UiPreviewRoot)
		{
			result.ok = false;
			result.error = "layout_load_failed";
			return true;
		}

		int limit = UI_TREE_DEFAULT_LIMIT;
		if (args.limit > 0)
		{
			limit = args.limit;
		}
		if (limit > UI_TREE_MAX_LIMIT)
		{
			limit = UI_TREE_MAX_LIMIT;
		}

		CollectUiNodes(m_UiPreviewRoot, snap, limit);
		result.ui = snap;
		result.source = args.path;
		result.ok = true;
		Log(string.Format("ui_reload_layout loaded %1 nodes=%2", args.path, snap.nodes.Count()));
		return true;
	}

	//! Keyboard-focus a named widget. SetActiveWindow(resetFocus=false) first so
	//! the engine does not steal focus onto the first focusable child
	//! (1_core\proto\enwidgets.c:694). ok is GetFocus()==target, not "SetFocus
	//! ran"; a widget without inputs (TextWidget, NoFocus) fails with
	//! focus_not_taken while found stays true (enwidgets.c:697).
	protected bool DispatchUiFocus(MCPCommand command, MCPResult result)
	{
		MCPArgs args;
		string error;
		Widget target;
		Widget topmost;
		Widget parent;
		Widget focused;
		bool activeOk;
		string activeStr;
		string okStr;
		MCPUiSnapshot snap;
		MCPUiNode node;

		if (!command.args || command.args.path == "")
		{
			result.ok = false;
			result.found = false;
			result.error = "bad_args";
			return true;
		}

		args = command.args;
		error = "";
		target = ResolveUiRoot(args, error);
		if (!target)
		{
			result.ok = false;
			result.found = false;
			result.error = error;
			return true;
		}

		result.found = true;

		topmost = target;
		parent = topmost.GetParent();
		while (parent)
		{
			topmost = parent;
			parent = topmost.GetParent();
		}

		activeOk = SetActiveWindow(topmost, false);
		SetFocus(target);

		focused = GetFocus();
		result.ok = (focused == target);
		if (focused)
		{
			result.source = focused.GetName();
		}
		else
		{
			result.source = "";
		}

		if (result.ok)
		{
			result.error = "";
		}
		else
		{
			result.error = "focus_not_taken";
		}

		snap = new MCPUiSnapshot();
		node = new MCPUiNode();
		FillUiNode(target, node);
		snap.nodes.Insert(node);
		result.ui = snap;

		// Enforce: a bool is not concatenated into a string anywhere in this file.
		// The one other place that logs one converts it first (:3221-3226); same here.
		activeStr = "0";
		if (activeOk)
		{
			activeStr = "1";
		}
		okStr = "0";
		if (result.ok)
		{
			okStr = "1";
		}
		Log("ui_focus path=" + args.path + " active=" + activeStr + " ok=" + okStr + " source=" + result.source);
		return true;
	}

	protected bool DispatchUiDialog(MCPCommand command, MCPResult result)
	{
		if (!ValidateUiDialogArgs(command.args))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (m_Dialog && m_Dialog.IsOpen())
		{
			MCPDialogResult rejected = new MCPDialogResult();
			rejected.state = "rejected";
			rejected.reason = "busy";
			rejected.elapsed_s = 0.0;
			result.ok = true;
			result.dialog = rejected;
			Log("client ui_dialog rejected reason=busy");
			return true;
		}

		if (!EnsureDialogHost())
		{
			result.ok = false;
			result.error = DialogHostError();
			return true;
		}

		float timeoutS = 60.0;
		if (command.args.timeout_s > 0.0)
		{
			timeoutS = command.args.timeout_s;
		}

		MCPDialogSpec spec = new MCPDialogSpec();
		spec.kind = command.args.kind;
		spec.title = command.args.title;
		spec.message = command.args.message;
		spec.timeout_s = timeoutS;
		if (command.args.fields)
		{
			spec.fields = command.args.fields;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "ui_dialog";
		job.args = command.args;
		job.dialog = new MCPDialogResult();
		job.deadline_s = m_JobRunner.GetElapsedS() + timeoutS + 5.0;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_Dialog.SetResultTarget(job.dialog);
		m_Dialog.SetClock(m_JobRunner);
		float deadlineS = m_JobRunner.GetElapsedS() + timeoutS;
		if (!m_Dialog.Open(spec, deadlineS))
		{
			result.ok = false;
			result.error = DialogHostError();
			return true;
		}

		m_JobRunner.AddJob(job);
		m_DialogJob = job;
		int fieldCount = 0;
		if (command.args.fields)
		{
			fieldCount = command.args.fields.Count();
		}

		Log("client job queued id=" + job.id + " kind=ui_dialog fields=" + fieldCount + " deadline_s=" + job.deadline_s);
		return false;
	}

	protected bool ValidateUiDialogArgs(MCPArgs args)
	{
		if (!args)
		{
			return false;
		}

		if (args.kind != "acknowledge" && args.kind != "confirm" && args.kind != "form")
		{
			return false;
		}

		if (args.title == "")
		{
			return false;
		}

		int fieldCount = 0;
		if (args.fields)
		{
			fieldCount = args.fields.Count();
		}

		if (fieldCount > 6)
		{
			return false;
		}

		if (args.kind == "form" && fieldCount < 1)
		{
			return false;
		}

		if (args.timeout_s != 0.0)
		{
			if (args.timeout_s < 5.0 || args.timeout_s > 240.0 || !IsFiniteFloat(args.timeout_s))
			{
				return false;
			}
		}

		int i = 0;
		while (i < fieldCount)
		{
			MCPDialogField field = args.fields.Get(i);
			if (!field || field.id == "")
			{
				return false;
			}

			i = i + 1;
		}

		return true;
	}

	protected bool EnsureDialogHost()
	{
		if (!m_Dialog)
		{
			m_Dialog = new MCPDialogController();
			m_DialogSink = new MCPClientDialogSink(this);
			m_Dialog.SetSink(m_DialogSink);
			m_Dialog.SetClock(m_JobRunner);
		}

		return m_Dialog.EnsureHost();
	}

	protected string DialogHostError()
	{
		if (m_Dialog)
		{
			string err = m_Dialog.GetLastHostError();
			if (err != "")
			{
				return err;
			}
		}

		return "host_create_failed";
	}

	void AcceptDialogResult(MCPDialogResult dialog)
	{
		if (!dialog)
		{
			return;
		}

		Log("dialog result state=" + dialog.state + " reason=" + dialog.reason);
	}

	// Starts a user action on the local player. Returns after PerformActionStart;
	// UseAcknowledgment() is true (actionbase.c:1146-1148) so the server still
	// re-evaluates. Waiting here would freeze the sim tick.
	protected bool DispatchActionUse(MCPCommand command, MCPResult result)
	{
		result.started = false;

		if (!command.args || command.args.action == "")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			result.ok = false;
			result.error = "no_player";
			return true;
		}

		ActionManagerClient amc = ActionManagerClient.Cast(player.GetActionManager());
		if (!amc)
		{
			result.ok = false;
			result.error = "no_action_manager";
			return true;
		}

		string wantedAction = command.args.action;
		ActionBase action;
		if (ActionManagerBase.m_ActionsArray)
		{
			int actionIndex = 0;
			while (actionIndex < ActionManagerBase.m_ActionsArray.Count() && !action)
			{
				ActionBase candidate = ActionManagerBase.m_ActionsArray.Get(actionIndex);
				if (candidate && candidate.Type().ToString() == wantedAction)
				{
					action = candidate;
				}

				actionIndex = actionIndex + 1;
			}
		}

		if (!action)
		{
			result.ok = false;
			result.error = "action_not_found";
			return true;
		}

		result.action = action.Type().ToString();

		vector searchPos;
		if (command.args.pos && command.args.pos.Count() > 0)
		{
			if (!ArrayToVector(command.args.pos, searchPos))
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		else
		{
			searchPos = player.GetPosition();
		}

		float searchRadius = ACTION_USE_DEFAULT_RADIUS;
		if (command.args.radius > 0.0 && IsFiniteFloat(command.args.radius))
		{
			searchRadius = command.args.radius;
		}

		string classFilter = command.args.classname;
		Object targetObj = FindNearestObjectNearClient(searchPos, searchRadius, classFilter, player);
		if (!targetObj)
		{
			result.ok = false;
			result.error = "target_not_found";
			return true;
		}

		// cursorHitPos must be a real point on the target. CCTCursor.Can
		// measures DistanceSq from GetCursorHitPos (cctcursor.c:30-33).
		// ForceTarget writes vector.Zero (actionmanagerclient.c:466), which
		// is ~10 km from the player on Chernarus and always fails.
		vector cursorHitPos = targetObj.GetPosition();
		ActionTarget actionTarget = new ActionTarget(targetObj, null, -1, cursorHitPos, 0);

		result.classname = targetObj.GetType();
		result.pos_real = new array<float>();
		VectorToArray(cursorHitPos, result.pos_real);
		result.distance = vector.Distance(player.GetPosition(), cursorHitPos);

		if (amc.GetRunningAction() != null)
		{
			result.ok = false;
			result.error = "action_in_progress";
			return true;
		}

		if (!amc.ActionPossibilityCheck(player.GetCurrentCommandID()))
		{
			result.ok = false;
			result.error = "not_possible";
			return true;
		}

		if (!ScriptInputUserData.CanStoreInputUserData())
		{
			result.ok = false;
			result.error = "input_busy";
			return true;
		}

		// Held item is the ItemBase in the local player's hands; empty hands stay null.
		// ActionInteractBase.UseMainItem() is false (actioninteractbase.c:71-74).
		// CCINone is not empty-hands.
		ItemBase heldItem = player.GetItemInHands();
		if (!action.Can(player, actionTarget, heldItem))
		{
			result.ok = false;
			result.error = "condition_failed";
			return true;
		}

		amc.PerformActionStart(action, actionTarget, heldItem, NULL);
		// PerformActionStart is void. ActionStart can fail in SetupAction
		// (typically inventory reservation) and return without ctx.Send
		// (actionmanagerclient.c:638-643). GetRunningAction() is null then.
		if (amc.GetRunningAction() == null)
		{
			result.started = false;
			result.ok = false;
			result.error = "setup_failed";
			return true;
		}

		result.started = true;
		result.ok = true;
		return true;
	}

	protected Object FindNearestObjectNearClient(vector pos, float radius, string classFilter, Object skip)
	{
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, radius, m_ReadyObjects, m_ReadyProxyCargos);

		Object best;
		float bestDist = 0.0;
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			if (found && found != skip)
			{
				bool classOk = classFilter == "";
				if (!classOk)
				{
					if (found.GetType() == classFilter)
					{
						classOk = true;
					}
					else if (found.ClassName() == classFilter)
					{
						classOk = true;
					}
				}

				if (classOk)
				{
					float distSq = vector.DistanceSq(pos, found.GetPosition());
					if (!best)
					{
						best = found;
						bestDist = distSq;
					}
					else if (distSq < bestDist)
					{
						best = found;
						bestDist = distSq;
					}
				}
			}

			i = i + 1;
		}

		return best;
	}

	protected Widget ResolveUiRoot(MCPArgs args, out string error)
	{
		error = "";
		if (!GetGame())
		{
			error = "no_game";
			return null;
		}

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		if (!workspace)
		{
			error = "no_workspace";
			return null;
		}

		if (args && args.path != "")
		{
			Widget named = workspace.FindAnyWidget(args.path);
			if (!named)
			{
				named = FindWidgetByNameWalk(workspace, args.path);
			}
			if (!named)
			{
				error = "widget_not_found";
				return null;
			}
			return named;
		}

		UIManager ui = GetGame().GetUIManager();
		if (ui)
		{
			UIScriptedMenu menu = ui.GetMenu();
			if (menu)
			{
				Widget menuRoot = menu.GetLayoutRoot();
				if (menuRoot)
				{
					return menuRoot;
				}
			}
		}

		error = "no_menu";
		return null;
	}

	protected Widget FindWidgetByNameWalk(Widget start, string name)
	{
		if (!start)
		{
			return null;
		}

		if (start.GetName() == name)
		{
			return start;
		}

		Widget child = start.GetChildren();
		while (child)
		{
			Widget found = FindWidgetByNameWalk(child, name);
			if (found)
			{
				return found;
			}

			child = child.GetSibling();
		}

		return null;
	}

	protected void FillUiNode(Widget w, MCPUiNode node)
	{
		if (!w || !node)
		{
			return;
		}

		node.name = w.GetName();
		node.type = w.GetTypeName();
		node.user_id = w.GetUserID();
		node.visible = w.IsVisible();
		node.visible_hierarchy = w.IsVisibleHierarchy();
		node.disabled = false;
		int flags = w.GetFlags();
		if (flags & WidgetFlags.DISABLED)
		{
			node.disabled = true;
		}
		node.ignore_pointer = false;
		if (flags & WidgetFlags.IGNOREPOINTER)
		{
			node.ignore_pointer = true;
		}
		node.color = w.GetColor();
		float sx;
		float sy;
		float sw;
		float sh;
		w.GetScreenPos(sx, sy);
		w.GetScreenSize(sw, sh);
		node.screen_x = sx;
		node.screen_y = sy;
		node.screen_w = sw;
		node.screen_h = sh;
		node.text_readable = false;
		node.text = "";

		EditBoxWidget editBox = EditBoxWidget.Cast(w);
		if (editBox)
		{
			node.text = editBox.GetText();
			node.text_readable = true;
			return;
		}

		MultilineEditBoxWidget multi = MultilineEditBoxWidget.Cast(w);
		if (multi)
		{
			string multiText;
			multi.GetText(multiText);
			node.text = multiText;
			node.text_readable = true;
			return;
		}

		ButtonWidget btn = ButtonWidget.Cast(w);
		if (btn)
		{
			string btnText;
			btn.GetText(btnText);
			node.text = btnText;
			node.text_readable = true;
		}
	}

	protected void CollectUiNodes(Widget w, MCPUiSnapshot snap, int limit)
	{
		if (!w)
		{
			return;
		}
		if (!snap)
		{
			return;
		}
		if (!snap.nodes)
		{
			return;
		}
		if (snap.nodes.Count() >= limit)
		{
			return;
		}

		MCPUiNode node = new MCPUiNode();
		FillUiNode(w, node);
		snap.nodes.Insert(node);

		Widget child = w.GetChildren();
		while (child)
		{
			CollectUiNodes(child, snap, limit);
			if (snap.nodes.Count() >= limit)
			{
				return;
			}
			child = child.GetSibling();
		}
	}

	//! Returns whether a handler CONSUMED the click, not whether one was found.
	//! ScriptedWidgetEventHandler.OnClick reports that in its return value
	//! (1_core\proto\enwidgets.c:658). handlerName is set as soon as a handler is
	//! located, so the caller can tell an absent handler from a declining one.
	protected bool InvokeUiClick(Widget target, int mouseButton, out string handlerName)
	{
		handlerName = "";
		if (!GetGame())
		{
			return false;
		}

		Widget cursor = target;
		while (cursor)
		{
			Class scriptInst;
			cursor.GetScript(scriptInst);
			ScriptedWidgetEventHandler scriptHandler = ScriptedWidgetEventHandler.Cast(scriptInst);
			if (scriptHandler)
			{
				handlerName = scriptInst.ClassName();
				return scriptHandler.OnClick(target, 0, 0, mouseButton);
			}
			if (scriptInst)
			{
				bool scriptConsumed = false;
				int scriptCalled = g_Game.GameScript.CallFunctionParams(scriptInst, "OnClick", scriptConsumed, new Param4<Widget, int, int, int>(target, 0, 0, mouseButton));
				if (scriptCalled)
				{
					handlerName = scriptInst.ClassName();
					return scriptConsumed;
				}
			}

			Class userInst;
			cursor.GetUserData(userInst);
			ScriptedWidgetEventHandler userHandler = ScriptedWidgetEventHandler.Cast(userInst);
			if (userHandler)
			{
				handlerName = userInst.ClassName();
				return userHandler.OnClick(target, 0, 0, mouseButton);
			}
			if (userInst)
			{
				bool userConsumed = false;
				int userCalled = g_Game.GameScript.CallFunctionParams(userInst, "OnClick", userConsumed, new Param4<Widget, int, int, int>(target, 0, 0, mouseButton));
				if (userCalled)
				{
					handlerName = userInst.ClassName();
					return userConsumed;
				}
			}

			cursor = cursor.GetParent();
		}

		UIManager ui = GetGame().GetUIManager();
		if (ui)
		{
			UIScriptedMenu menu = ui.GetMenu();
			if (menu)
			{
				handlerName = menu.ClassName();
				return menu.OnClick(target, 0, 0, mouseButton);
			}
		}

		return false;
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
		else if (job.kind == "ui_dialog")
		{
			if (m_Dialog)
			{
				m_Dialog.Tick(m_JobRunner.GetElapsedS());
			}

			return false;
		}

		return false;
	}

	override bool MCP_IsJobReady(MCPJob job)
	{
		if (job && job.kind == "ui_dialog" && job.dialog && job.dialog.state != "")
		{
			return true;
		}

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

	protected Transport SelectVehicleGetInTransport(MCPJob job, vector pos, string expectedType)
	{
		Transport selected;
		Transport vehicle;
		Object found;
		int i;
		int matches;

		if (!job)
		{
			return null;
		}

		if (expectedType == "")
		{
			selected = FindTransportNearClient(pos);
			if (!selected)
			{
				job.error = "no_vehicle";
			}
			return selected;
		}

		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, DRIVE_CLIENT_SEARCH_RADIUS, m_ReadyObjects, m_ReadyProxyCargos);

		selected = null;
		matches = 0;
		i = 0;
		while (i < m_ReadyObjects.Count())
		{
			found = m_ReadyObjects.Get(i);
			vehicle = Transport.Cast(found);
			if (vehicle && vehicle.GetType() == expectedType)
			{
				matches = matches + 1;
				selected = vehicle;
			}

			i = i + 1;
		}

		if (matches == 0)
		{
			job.error = "no_vehicle";
			return null;
		}

		if (matches != 1)
		{
			job.error = "bad_args";
			return null;
		}

		return selected;
	}

	protected bool ProcessVehicleGetInClientPrep(MCPJob job)
	{
		PlayerBase player;
		HumanCommandVehicle vehicleCommand;
		vector seatPos;
		Transport foundCar;
		Transport observed;
		int seatIndex;
		int seatAnim;
		int crewSize;
		int crewIndex;
		string expectedType;
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

		seatIndex = 0;
		expectedType = "";
		if (job.args)
		{
			seatIndex = job.args.seat;
			expectedType = job.args.type;
		}

		if (seatIndex < 0 || seatIndex > 63)
		{
			job.error = "bad_args";
			return true;
		}

		foundCar = Transport.Cast(job.subject);
		if (!foundCar)
		{
			if (!job.args || !ArrayToVector(job.args.pos, seatPos))
			{
				job.error = "no_pos";
				return true;
			}

			foundCar = SelectVehicleGetInTransport(job, seatPos, expectedType);
			if (!foundCar)
			{
				if (job.error == "")
				{
					job.error = "no_vehicle";
				}
				return true;
			}

			crewSize = foundCar.CrewSize();
			if (seatIndex >= crewSize)
			{
				job.error = "bad_args";
				return true;
			}

			job.subject = foundCar;
		}

		vehicleCommand = player.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			if (!job.seat_attempted)
			{
				seatAnim = foundCar.GetSeatAnimationType(seatIndex);
				started = player.StartCommand_Vehicle(foundCar, seatIndex, seatAnim);
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

		observed = vehicleCommand.GetTransport();
		if (!observed)
		{
			job.error = "no_vehicle";
			return true;
		}

		if (!job.subject || observed != job.subject)
		{
			job.error = "not_seated";
			return true;
		}

		crewIndex = observed.CrewMemberIndex(player);
		if (crewIndex != seatIndex)
		{
			job.error = "not_seated";
			return true;
		}

		job.subject = observed;
		car = CarScript.Cast(observed);
		if (seatIndex == 0 && car)
		{
			if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)
			{
				job.error = "not_seated";
				return true;
			}

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

		job.phase = DRIVE_CLIENT_PHASE_REPORT;
		return true;
	}

	protected string VehicleGetInSeatToken(int vehicleSeat)
	{
		if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			return "driver";
		}
		else if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_CODRIVER)
		{
			return "codriver";
		}
		else if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_PASSENGER_L)
		{
			return "passenger_left";
		}
		else if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_PASSENGER_R)
		{
			return "passenger_right";
		}

		return "unknown";
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

		// Switching away from an owned staticcamera has to drop it first: the
		// assignments at the end of this method overwrite m_ActiveCam and clear
		// m_ActiveCamOwned, which would strand the old camera in the world with no
		// reference left to delete it. Bare DeleteOwnedCamera, not ReleaseCamera,
		// to match what the static path already does when it replaces a camera.
		DeleteOwnedCamera();

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
			PlayerBase getInPlayer;
			HumanCommandVehicle getInCommand;
			Transport getInTransport;
			int getInCrewIndex;
			MCPResult resultGetIn = new MCPResult();
			resultGetIn.id = job.id;
			resultGetIn.ok = true;
			resultGetIn.seated = false;
			resultGetIn.seat = "unknown";
			resultGetIn.type = "";
			resultGetIn.classname = "";
			getInPlayer = PlayerBase.Cast(GetGame().GetPlayer());
			if (getInPlayer)
			{
				getInCommand = getInPlayer.GetCommand_Vehicle();
				if (getInCommand)
				{
					getInTransport = getInCommand.GetTransport();
					if (getInTransport)
					{
						resultGetIn.type = getInTransport.GetType();
						resultGetIn.classname = getInTransport.ClassName();
						resultGetIn.seat = VehicleGetInSeatToken(getInCommand.GetVehicleSeat());
						getInCrewIndex = getInTransport.CrewMemberIndex(getInPlayer);
						if (getInCrewIndex >= 0)
						{
							resultGetIn.seated = true;
						}
					}
				}
			}
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

		if (job.kind == "ui_dialog")
		{
			PostUiDialogJob(job);
		}
	}

	override void MCP_PostJobFailure(MCPJob job)
	{
		if (job && job.kind == "drive_probe_client")
		{
			MCPCarDrive.Clear();
			RestoreGameplay();
		}

		if (job && job.kind == "ui_dialog")
		{
			if (m_Dialog && m_Dialog.IsOpen())
			{
				m_Dialog.FinishDisconnected();
			}

			PostUiDialogJob(job);
			return;
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

		if (job.kind == "ui_dialog")
		{
			if (m_Dialog && m_Dialog.IsOpen())
			{
				m_Dialog.Tick(job.deadline_s);
			}

			PostUiDialogJob(job);
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

	protected void PostUiDialogJob(MCPJob job)
	{
		if (!job)
		{
			return;
		}

		MCPResult result = new MCPResult();
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		if (job.dialog && job.dialog.state != "")
		{
			result.dialog = job.dialog;
			result.ok = true;
			result.error = "";
		}
		else
		{
			result.ok = false;
			if (job.error != "")
			{
				result.error = job.error;
			}
			else
			{
				result.error = "dialog_no_state";
			}
		}

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
		if (m_DialogJob == job)
		{
			m_DialogJob = null;
		}
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

		// Defense in depth for the Dispatch readiness gate: the native
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

	// RestoreGameplay covers simulation, controls and HUD and never the
	// camera, so the restore_gameplay command answered ok:1 with the view still
	// locked to the debug camera -- and camera_set has no off mode, so reconnecting
	// was the only way out. Shutdown already performed the full teardown; sharing
	// it is what stops the two paths drifting apart again.
	// Ownership is the subtlety: FreeDebugCamera is a singleton the bridge does not
	// own and is only deactivated, while the staticcamera built for
	// orient/lookat/matrix is owned and must also be deleted.
	protected void ReleaseCamera()
	{
		if (m_ActiveCam)
		{
			m_ActiveCam.SetActive(false);
		}

		DeleteOwnedCamera();
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
		string resultRequest = "result?key=" + m_Key;
		if (m_PeerInstance != "")
		{
			resultRequest = resultRequest + "&inst=" + EncodeQueryValue(m_PeerInstance);
		}
		m_Ctx.POST(cb, resultRequest, body);
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
		int i;
		i = 0;
		if (m_CallbackRefs)
		{
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

		if (!m_PollCallbackRefs)
		{
			return;
		}

		i = 0;
		while (i < m_PollCallbackRefs.Count())
		{
			if (m_PollCallbackRefs.Get(i) == cb)
			{
				m_PollCallbackRefs.Remove(i);
				return;
			}

			i = i + 1;
		}
	}

	void Shutdown()
	{
		bool postedTerminal = false;
		if (m_Dialog && m_Dialog.IsOpen())
		{
			m_Dialog.FinishDisconnected();
		}

		// Shutdown is only reached from ~MissionGameplay / ~MCPClientBridge, so
		// no later OnTick exists on this mission. reset() clears pending REST
		// requests (restapi.c:130-133). After a terminal dialog POST, skip it
		// and keep callback refs: RestApi is process-scoped and can finish the
		// already-pushed request. A delayed reset() is unsafe because
		// GetRestContext reuses the same context for the next mission.
		if (m_DialogJob && m_DialogJob.dialog && m_DialogJob.dialog.state != "")
		{
			PostUiDialogJob(m_DialogJob);
			postedTerminal = true;
			Log("client shutdown posted dialog terminal; rest reset skipped");
		}

		// The preview root outlives the mission otherwise: the workspace survives
		// a mission change and the next one would start with a stale tree on top.
		if (m_UiPreviewRoot)
		{
			m_UiPreviewRoot.Unlink();
			m_UiPreviewRoot = null;
		}

		if (m_Dialog)
		{
			m_Dialog.DestroyHost();
			m_Dialog.SetSink(null);
			m_Dialog.SetResultTarget(null);
			m_Dialog = null;
		}

		m_DialogSink = null;
		m_DialogJob = null;
		m_DialogHostTried = false;
		MCPVehicleTrace.Abort("shutdown");
		MCPCarDrive.Clear();
		RestoreGameplay();
		ReleaseCamera();

		// Break the callback->bridge->callback-array ref cycle before
		// contexts are dropped. Shared context plus a posted terminal
		// POST keeps the arrays, so a never-completing GET must not keep
		// a live m_Bridge pointer that pins the bridge across missions.
		int i;
		if (m_PollCallbackRefs)
		{
			i = 0;
			while (i < m_PollCallbackRefs.Count())
			{
				MCPClientPollCallback pollCb = MCPClientPollCallback.Cast(m_PollCallbackRefs.Get(i));
				if (pollCb)
				{
					pollCb.DetachBridge();
				}

				i = i + 1;
			}
		}

		if (m_CallbackRefs)
		{
			i = 0;
			while (i < m_CallbackRefs.Count())
			{
				MCPClientResultCallback resultCb = MCPClientResultCallback.Cast(m_CallbackRefs.Get(i));
				if (resultCb)
				{
					resultCb.DetachBridge();
				}

				i = i + 1;
			}
		}

		bool pollDistinct = false;
		if (m_PollCtx && m_PollCtx != m_Ctx)
		{
			pollDistinct = true;
		}
		// postedTerminal keeps the result POST alive. Poll is independent:
		// an unanswered GET must not pin the bridge across missions.
		if (pollDistinct)
		{
			m_PollCtx.reset();
		}
		m_PollCtx = null;

		if (m_Ctx)
		{
			if (!postedTerminal)
			{
				m_Ctx.reset();
			}
			m_Ctx = null;
		}

		if (m_PollCallbackRefs)
		{
			if (pollDistinct || !postedTerminal)
			{
				m_PollCallbackRefs.Clear();
			}
		}

		// Contexts already nulled above. Drop the live poll pointer after
		// teardown so a shared-ctx terminal POST path does not leave it set.
		m_PollCallback = null;

		if (m_CallbackRefs)
		{
			if (!postedTerminal)
			{
				m_CallbackRefs.Clear();
			}
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
