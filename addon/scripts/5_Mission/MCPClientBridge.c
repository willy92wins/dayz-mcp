class MCPClientPollCallback : RestCallback
{
	// MCPJobRunnerOwner is not Managed: detach explicitly; a raw link can dangle.
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
				DetachBridge();
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
				DetachBridge();
				return;
			}
			m_Bridge.OnPollError(errorCode);
			DetachBridge();
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			if (!m_Bridge.IsActivePollCallback(this))
			{
				DetachBridge();
				return;
			}
			m_Bridge.OnPollTimeout();
			DetachBridge();
		}
	}
};

class MCPClientResultCallback : RestCallback
{
	// MCPJobRunnerOwner is not Managed: detach explicitly; a raw link can dangle.
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
			DetachBridge();
		}
	}

	override void OnError(int errorCode)
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultError(errorCode);
			DetachBridge();
		}
	}

	override void OnTimeout()
	{
		if (m_Bridge)
		{
			m_Bridge.ReleaseCallback(this);
			m_Bridge.OnResultTimeout();
			DetachBridge();
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

//! Scratch for the UI resolver: how many widgets under a scope carry a name
//! and the first one seen. The walk stops counting at two, which is all the
//! contract distinguishes (0, 1, more).
class MCPUiNameMatch
{
	int count;
	Widget first;

	void MCPUiNameMatch()
	{
		count = 0;
		first = null;
	}
};

//! input_trigger (e1ae part 2): the one key this bridge holds. The press is
//! delivered at dispatch. The release comes from the phase (click on the next
//! tick, hold once hold_s has passed), from phase=release, from the press TTL,
//! from RestoreGameplay, when the local player changes or dies, and at
//! shutdown: nothing stays pressed without a scheduled release. Maintained
//! from MCPClientBridge.OnTick on the MCPWeaponControl.MaintainFromTick
//! pattern. Entry game is DayZGame.OnKeyPress/OnKeyRelease (dayzgame.c:2804-2918:
//! modifier flags, the keyboard handler, then the mission); it never presses
//! F4, LMENU or RMENU (IsExitComboKey). Entry mission is
//! Mission.OnKeyPress/OnKeyRelease (gameplay.c:709-710), what key_press calls.
//! Only the call is known: the handlers return nothing.
class MCPInputTriggerControl
{
	// Mirrored by loopback.INPUT_TRIGGER_*; tools/tests keep the copies equal.
	// hold_s and the press TTL are refused outside (0, max], not clamped.
	static const int DIK_MAX = 255;
	static const float HOLD_MAX_S = 10.0;
	static const float PRESS_MAX_TTL_S = 30.0;

	// Counts MaintainFromTick calls, one per bridge OnTick. Its own counter,
	// so a new bridge instance cannot move it back under a held press tick.
	static int s_Tick;
	static bool s_Held;
	static int s_Dik;
	static string s_Entry;
	static string s_Edge;
	static int s_PressTick;
	static float s_DueS;
	static PlayerBase s_Player;
	static int s_Gen;
	// The last release, read by the job that waits on it and by not_held.
	static int s_ReleasedGen;
	static int s_ReleasedDik;
	static string s_ReleasedEntry;
	static string s_ReleasedBy;
	static int s_ReleaseTick;
	static bool s_ReleaseDelivered;

	static bool IsHeld()
	{
		return s_Held;
	}

	static bool Holds(string entry, int dik)
	{
		if (!s_Held)
		{
			return false;
		}
		if (s_Entry != entry)
		{
			return false;
		}
		return s_Dik == dik;
	}

	static string HeldEdge()
	{
		if (!s_Held)
		{
			return "";
		}
		return s_Edge;
	}

	// DayZGame.OnKeyPress sets its left Alt flag on KC_LMENU and calls
	// RequestExit for F4 while that flag is set (dayzgame.c:2855-2858,
	// :2876-2881, DEVELOPER builds). The flag is private and a physical Alt
	// sets it too, so entry game never delivers F4, nor an Alt key that would
	// leave the flag set for a physical F4. KC_RMENU is refused as well:
	// vanilla tests the left flag twice (:2877), and a fix there would count it.
	static bool IsExitComboKey(int dik)
	{
		if (dik == KeyCode.KC_F4)
		{
			return true;
		}
		if (dik == KeyCode.KC_LMENU)
		{
			return true;
		}
		if (dik == KeyCode.KC_RMENU)
		{
			return true;
		}
		return false;
	}

	static int Generation()
	{
		return s_Gen;
	}

	static int PressTick()
	{
		return s_PressTick;
	}

	static bool WasReleased(int generation)
	{
		return s_ReleasedGen == generation;
	}

	static bool LastReleaseWas(string entry, int dik)
	{
		if (s_ReleasedGen <= 0)
		{
			return false;
		}
		if (s_ReleasedEntry != entry)
		{
			return false;
		}
		return s_ReleasedDik == dik;
	}

	static string ReleasedBy()
	{
		return s_ReleasedBy;
	}

	static int ReleaseTick()
	{
		return s_ReleaseTick;
	}

	static bool ReleaseDelivered()
	{
		return s_ReleaseDelivered;
	}

	// false when nothing was called: no game, or entry mission without a mission.
	static bool Deliver(string entry, int dik, bool press)
	{
		Mission mission;
		if (!GetGame())
		{
			return false;
		}
		if (entry == "game")
		{
			if (press)
			{
				GetGame().OnKeyPress(dik);
			}
			else
			{
				GetGame().OnKeyRelease(dik);
			}
			return true;
		}
		if (entry != "mission")
		{
			return false;
		}
		mission = GetGame().GetMission();
		if (!mission)
		{
			return false;
		}
		if (press)
		{
			mission.OnKeyPress(dik);
		}
		else
		{
			mission.OnKeyRelease(dik);
		}
		return true;
	}

	// Delivers the press, then arms its release. -1 while a key is held and 0
	// when nothing was delivered; nothing new is held in either case.
	static int Press(string entry, int dik, string edge, float dueS, PlayerBase player)
	{
		if (s_Held)
		{
			return -1;
		}
		if (!Deliver(entry, dik, true))
		{
			return 0;
		}
		s_Gen = s_Gen + 1;
		s_Held = true;
		s_Dik = dik;
		s_Entry = entry;
		s_Edge = edge;
		s_PressTick = s_Tick;
		s_DueS = dueS;
		s_Player = player;
		return s_Gen;
	}

	// Delivers OnKeyRelease on the held key's entry and records why. The held
	// state is cleared first, so a handler that reaches here again finds
	// nothing to release.
	static void ReleaseAll(string why)
	{
		if (!s_Held)
		{
			return;
		}
		s_Held = false;
		s_Player = null;
		s_ReleasedGen = s_Gen;
		s_ReleasedDik = s_Dik;
		s_ReleasedEntry = s_Entry;
		s_ReleasedBy = why;
		s_ReleaseTick = s_Tick;
		s_ReleaseDelivered = false;
		s_ReleaseDelivered = Deliver(s_Entry, s_Dik, false);
	}

	// Returns before any engine call while no key is held. A dead, changed or
	// missing local player releases at once; otherwise the release waits for
	// a later tick than the press.
	static void MaintainFromTick()
	{
		PlayerBase live;
		s_Tick = s_Tick + 1;
		if (!s_Held)
		{
			return;
		}
		live = null;
		if (GetGame())
		{
			live = PlayerBase.Cast(GetGame().GetPlayer());
		}
		if (!live)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (live != s_Player)
		{
			ReleaseAll("player_changed");
			return;
		}
		if (!live.IsAlive())
		{
			ReleaseAll("player_changed");
			return;
		}
		if (s_Tick <= s_PressTick)
		{
			return;
		}
		if (s_Edge == "click")
		{
			ReleaseAll("phase");
			return;
		}
		if (GetGame().GetTickTime() < s_DueS)
		{
			return;
		}
		if (s_Edge == "hold")
		{
			ReleaseAll("phase");
			return;
		}
		ReleaseAll("ttl");
	}
};

class MCPClientBridge extends MCPJobRunnerOwner
{
	protected const int MAX_DISPATCH_PER_TICK = 4;
	protected const int MAX_PENDING = 16;
	protected const int PENDING_POLL_THRESHOLD = 8;
	protected const int MAX_CALLBACK_REFS = 128;
	// One poll accepts at most MAX_DISPATCH_PER_TICK dispatched plus MAX_PENDING
	// queued; QueuePendingOrFail refuses the rest. Reserve exactly that much.
	protected const int MAX_POLL_RESULTS = 20;
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
	// f47b: OnTick calls a staticcamera release spends on FreeDebugCamera before
	// it leaves that one too (BeginCameraHandoff / FinishCameraHandoff).
	protected const int CAMERA_HANDOFF_TICKS = 10;
	// Seated apply is observed via GetCurrentCameraTransform, not m_ActiveCam.
	// Cabin vs requested pose farther than this is an unmoved-cabin fail.
	protected const float CAMERA_SEATED_POSE_EPS_M = 0.05;
	protected const float DRIVE_CLIENT_TIMEOUT_S = 12.0;
	protected const float DRIVE_CLIENT_PREP_TIMEOUT_S = 5.0;
	protected const float VEHICLE_CONTROL_DEFAULT_TTL_S = 3.0;
	protected const float VEHICLE_CONTROL_MAX_TTL_S = 30.0;
	// One command tick is enough to read an override back. 2s fails closed
	// when CommandHandler never runs, ahead of the tool's own wait.
	protected const float WEAPON_READ_TIMEOUT_S = 2.0;
	protected const float DRIVE_CLIENT_SEARCH_RADIUS = 4.0;
	protected const int DRIVE_CLIENT_PHASE_PREP = 0;
	protected const int DRIVE_CLIENT_PHASE_IGNITE = 1;
	protected const int DRIVE_CLIENT_PHASE_REPORT = 4;
	protected const int UI_TREE_DEFAULT_LIMIT = 256;
	protected const int UI_TREE_MAX_LIMIT = 512;
	protected const float ACTION_USE_DEFAULT_RADIUS = 5.0;
	// action_use_door scans view-geometry components for GetDoorIndex == door.
	// A component that is not that door returns -1 (actionopendoors.c:39-45),
	// so the scan cannot stop on -1. Past this cap the result is
	// door_component_not_found. One native call per index, once per command.
	protected const int ACTION_USE_DOOR_COMPONENT_CAP = 512;
	// input_describe: printable ASCII name, and the selected alternative's keys.
	protected const int INPUT_NAME_MAX = 128;
	protected const int INPUT_KEY_MAX = 16;
	// input_trigger click and hold answer after their release. Past it by this
	// much, the job releases the key itself and answers aborted. The tool waits
	// hold_s plus the same 5 s for a hold.
	protected const float INPUT_TRIGGER_JOB_SLACK_S = 5.0;
	//! Capability census announced on every poll as `caps=`: the exact set of
	//! command.cmd branches Dispatch() handles before falling to unknown_command,
	//! sorted bytewise and comma-separated. tools/tests/test_bridge_client_capabilities.py
	//! cross-checks it against the dispatcher chain; the daemon compares it with
	//! the tools it registers. Written as short literals joined with +, split at
	//! commas (5_Mission\gui\chat\chatline.c:8): the longest single literal in
	//! vanilla is 237 bytes and this census is longer than that.
	protected const string CLIENT_POLL_CAPS = "action_use,action_use_door,action_use_target,anim_timeline,camera_get,camera_set,engine_set,input_describe,input_trigger,key_press,player_respawn," + "restore_gameplay,ui_click,ui_dialog,ui_focus,ui_reload_layout,ui_set_text,ui_tree," + "vehicle_control,vehicle_get_in_client,vehicle_release,vehicle_telemetry,vehicle_trace,weapon_aim,weapon_fire,weapon_raise,weapon_sights";

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
	protected bool m_Shutdown;
	protected bool m_ShutdownReentryLogged;
	protected bool m_RestoreNoGameLogged;
	protected bool m_ControlsSuppressed;
	protected bool m_PlayerSimulationDisabled;
	protected bool m_ActiveCamOwned;
	protected Camera m_ActiveCam;
	//! f47b release handoff: the staticcamera it deactivated (deleted when the
	//! handoff finishes; not ref, an entity the world owns) and its OnTick count.
	protected Camera m_CameraHandoffCam;
	protected bool m_CameraHandoffPending;
	protected int m_CameraHandoffTicks;
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
		m_Shutdown = false;
		m_ShutdownReentryLogged = false;
		m_RestoreNoGameLogged = false;
		m_ControlsSuppressed = false;
		m_PlayerSimulationDisabled = false;
		m_ActiveCamOwned = false;
		m_CameraHandoffPending = false;
		m_CameraHandoffTicks = 0;
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

	void OnMissionKeyPress(int key)
	{
		if (!m_Dialog)
		{
			return;
		}

		if (!m_Dialog.IsOpen())
		{
			return;
		}

		Log("OnKeyPress key=" + key);
		m_Dialog.TryCancelFromKey(key, m_JobRunner.GetElapsedS());
	}

	void OnTick(float timeslice)
	{
		m_Tick = m_Tick + 1;
		// Returns before any engine call while no weapon override is armed.
		MCPWeaponControl.MaintainFromTick();
		// Releases the input_trigger key when its phase, its TTL or the local
		// player says so. Before the job runner, so a click or hold answers in
		// the tick of its release. Returns before any engine call while no key is held.
		MCPInputTriggerControl.MaintainFromTick();
		// anim_timeline samples here on every frame, never from a job: the edge
		// sample belongs to the frame the action or callback state changed.
		// Returns before any engine call while no timeline is active.
		MCPAnimTimeline.Tick(timeslice);
		// Ahead of the job runner, so a restore_gameplay job posts in the very
		// tick the camera handoff finishes (f47b). Returns at once when none runs.
		TickCameraHandoff();

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

	// Accepted work that still owes a result: POSTs in flight, queued commands and
	// running jobs. The client counted only the queue; the server counts all three.
	protected int OutstandingWork()
	{
		int total = 0;
		if (m_CallbackRefs)
		{
			total = total + m_CallbackRefs.Count();
		}

		if (m_Pending)
		{
			total = total + m_Pending.Count();
		}

		if (m_JobRunner)
		{
			total = total + m_JobRunner.Count();
		}

		return total;
	}

	protected void StartPoll()
	{
		if (!m_PollCtx)
		{
			return;
		}

		// Pause admission only. OnTick ticks jobs and drains pending above the poll
		// decision, so the count keeps falling while polling is held.
		if (OutstandingWork() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS)
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
		request = request + "&caps=" + EncodeQueryValue(CLIENT_POLL_CAPS);
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
		int blocking;
		int weaponJobs;
		int triggerJobs;
		if (!m_JobRunner)
		{
			return false;
		}

		// weapon_action is a one-tick read-back. It must not refuse camera_set.
		// Nor does an input_trigger click or hold, which waits on a key release.
		blocking = m_JobRunner.CountExcluding("ui_dialog");
		weaponJobs = m_JobRunner.CountOfKind("weapon_action");
		triggerJobs = m_JobRunner.CountOfKind("input_trigger");
		blocking = blocking - weaponJobs - triggerJobs;
		return blocking > 0;
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
			// While a staticcamera release is still handing off to the free camera,
			// a job posts this reply once it has finished (f47b).
			postNow = !QueueRestoreGameplayJob(command, result);
		}
		else if (command.cmd == "key_press")
		{
			postNow = DispatchKeyPress(command, result);
		}
		else if (command.cmd == "player_respawn")
		{
			postNow = DispatchPlayerRespawn(command, result);
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
		else if (command.cmd == "action_use_door")
		{
			postNow = DispatchActionUse(command, result);
		}
		else if (command.cmd == "action_use_target")
		{
			postNow = DispatchActionUse(command, result);
		}
		else if (command.cmd == "anim_timeline")
		{
			postNow = DispatchAnimTimeline(command, result);
		}
		else if (command.cmd == "input_describe")
		{
			postNow = DispatchInputDescribe(command, result);
		}
		else if (command.cmd == "input_trigger")
		{
			postNow = DispatchInputTrigger(command, result);
		}
		else if (command.cmd == "weapon_aim")
		{
			postNow = DispatchWeaponAim(command, result);
		}
		else if (command.cmd == "weapon_fire")
		{
			postNow = DispatchWeaponFire(command, result);
		}
		else if (command.cmd == "weapon_raise")
		{
			postNow = DispatchWeaponRaise(command, result);
		}
		else if (command.cmd == "weapon_sights")
		{
			postNow = DispatchWeaponSights(command, result);
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

	// Printable ASCII (codes 32..126), 1..INPUT_NAME_MAX. Same bound as the ingress.
	protected bool IsPrintableInputName(string value)
	{
		if (value == "")
		{
			return false;
		}

		int length = value.Length();
		if (length > INPUT_NAME_MAX)
		{
			return false;
		}

		int i = 0;
		while (i < length)
		{
			string character = value.Substring(i, 1);
			int code = character.ToAscii();
			if (code < 32 || code > 126)
			{
				return false;
			}

			i = i + 1;
		}

		return true;
	}

	// Read GetInputByName. exists is input.ID() >= 0, the input index
	// (uainput.c:25). In 1.29 an unknown name is a shared placeholder whose
	// index is -1 (ficha 4f50): exists stays false and no bind is read.
	// The probe is still published for that placeholder, so a caller can
	// see why exists is false. SelectAlternative is not called: it would
	// change which bind is active. LocalPress is not called.
	protected bool DispatchInputDescribe(MCPCommand command, MCPResult result)
	{
		if (!command.args || !IsPrintableInputName(command.args.name))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		UAInputAPI api = GetUApi();
		if (!api)
		{
			result.ok = false;
			result.error = "input_api_unavailable";
			return true;
		}

		UAInput input = api.GetInputByName(command.args.name);
		MCPInputDescribe described = new MCPInputDescribe();
		if (!input)
		{
			// Probe stays unassigned. A null GetInputByName is exists false.
			described.exists = false;
			result.input_describe = described;
			result.ok = true;
			return true;
		}

		// Index first. Below 0 is the shared placeholder and must not reach
		// a bind read. The same int is what the probe publishes as input_id.
		int inputId = input.ID();
		if (inputId >= 0)
		{
			int bindingCount = input.BindingCount();
			int keyCount = input.BindKeyCount();
			int conflictCount = input.ConflictCount();
			if (bindingCount < 0 || keyCount < 0 || keyCount > INPUT_KEY_MAX || conflictCount < 0)
			{
				result.ok = false;
				result.error = "input_bind_unreadable";
				return true;
			}

			described.exists = true;
			described.binding_count = bindingCount;
			described.locked = input.IsLocked();
			described.conflict_count = conflictCount;

			int keyIndex = 0;
			while (keyIndex < keyCount)
			{
				MCPInputKey bound = new MCPInputKey();
				bound.index = keyIndex;
				bound.key_code = input.GetBindKey(keyIndex);
				bound.device = input.GetBindDevice(keyIndex);
				described.keys.Insert(bound);
				keyIndex = keyIndex + 1;
			}
		}

		// Probe for every non-null hit, including the placeholder, so a
		// caller can see why exists is false. One active-input array per
		// call. NameHash is stored so GetInputByID and the active-list
		// scan use the same value.
		MCPInputProbe probe = new MCPInputProbe();
		int nameHash = input.NameHash();
		probe.input_id = inputId;
		probe.name_hash = nameHash;
		probe.name_string_hash = command.args.name.Hash();
		probe.by_id_found = false;
		probe.by_id_same_hash = false;
		UAInput byId = api.GetInputByID(inputId);
		if (byId)
		{
			probe.by_id_found = true;
			int byIdHash = byId.NameHash();
			if (byIdHash == nameHash)
			{
				probe.by_id_same_hash = true;
			}
		}
		probe.in_active_inputs = false;
		TIntArray activeIds = new TIntArray();
		api.GetActiveInputs(activeIds);
		int activeIndex = activeIds.Find(inputId);
		if (activeIndex >= 0)
		{
			probe.in_active_inputs = true;
		}
		described.probe = probe;

		result.input_describe = described;
		result.ok = true;
		return true;
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

	// input_trigger (e1ae part 2). kind key delivers one DIK code to a script
	// key handler and releases it (MCPInputTriggerControl). kind input resolves
	// a UAInput and refuses: 1.29 has no script setter for UAInput.Local*.
	protected bool DispatchInputTrigger(MCPCommand command, MCPResult result)
	{
		MCPArgs args;
		MCPInputTrigger reply;
		if (!m_JobRunner)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		// Client only. The Dispatch gate already needs a local player.
		if (GetGame().IsDedicatedServer())
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		args = command.args;
		if (args.trigger_edge != "click" && args.trigger_edge != "hold" && args.trigger_edge != "press" && args.trigger_edge != "release")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (!InputTriggerTimesOk(args))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		reply = new MCPInputTrigger();
		reply.kind = args.trigger_kind;
		reply.phase = args.trigger_edge;
		reply.tick_time_s = GetGame().GetTickTime();
		if (args.trigger_kind == "key")
		{
			return DispatchInputTriggerKey(command, result, reply);
		}
		if (args.trigger_kind == "input")
		{
			return DispatchInputTriggerInput(command, result, reply);
		}
		result.ok = false;
		result.error = "bad_args";
		return true;
	}

	// hold_s with the hold edge and hold_ttl_s with the press edge, finite and
	// in (0, max]. An absent key arrives as 0 and is refused here (fb-8779).
	protected bool InputTriggerTimesOk(MCPArgs args)
	{
		if (args.trigger_edge == "hold")
		{
			if (!IsStrictFinite(args.hold_s))
			{
				return false;
			}
			if (args.hold_s <= 0.0)
			{
				return false;
			}
			if (args.hold_s > MCPInputTriggerControl.HOLD_MAX_S)
			{
				return false;
			}
		}
		if (args.trigger_edge == "press")
		{
			if (!IsStrictFinite(args.hold_ttl_s))
			{
				return false;
			}
			if (args.hold_ttl_s <= 0.0)
			{
				return false;
			}
			if (args.hold_ttl_s > MCPInputTriggerControl.PRESS_MAX_TTL_S)
			{
				return false;
			}
		}
		return true;
	}

	// One key at a time. click and hold answer after their release (a job);
	// press answers at once and stays held until release, its TTL, restore,
	// a player change or death, or shutdown.
	protected bool DispatchInputTriggerKey(MCPCommand command, MCPResult result, MCPInputTrigger reply)
	{
		MCPArgs args;
		UIManager ui;
		PlayerBase player;
		MCPJob job;
		string entry;
		string edge;
		int dik;
		int generation;
		float dueS;
		args = command.args;
		entry = args.trigger_entry;
		edge = args.trigger_edge;
		dik = args.dik;
		if (entry != "game" && entry != "mission")
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (dik < 0 || dik > MCPInputTriggerControl.DIK_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		reply.entry = entry;
		reply.dik = dik;
		ui = GetGame().GetUIManager();
		if (ui && ui.GetMenu())
		{
			reply.menu_open = true;
		}
		result.input_trigger = reply;
		// Entry game never presses F4, LMENU or RMENU, whatever this verb or the
		// physical keyboard holds (see IsExitComboKey). Entry mission reaches the
		// mission handlers without DayZGame's flags. A release goes on: none of
		// these keys can be held on entry game. Checked before busy.
		if (entry == "game" && edge != "release" && MCPInputTriggerControl.IsExitComboKey(dik))
		{
			result.ok = false;
			result.error = "would_request_exit";
			return true;
		}
		if (edge == "release")
		{
			return ReleaseInputTriggerKey(result, reply, entry, dik);
		}
		if (MCPInputTriggerControl.IsHeld() || m_JobRunner.CountOfKind("input_trigger") > 0)
		{
			result.ok = false;
			result.error = "input_trigger_busy";
			return true;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		dueS = -1.0;
		if (edge == "hold")
		{
			dueS = reply.tick_time_s + args.hold_s;
		}
		if (edge == "press")
		{
			dueS = reply.tick_time_s + args.hold_ttl_s;
		}
		generation = MCPInputTriggerControl.Press(entry, dik, edge, dueS, player);
		if (generation < 0)
		{
			result.ok = false;
			result.error = "input_trigger_busy";
			return true;
		}
		if (generation == 0)
		{
			result.ok = false;
			result.error = "no_mission";
			return true;
		}
		reply.delivered_press = true;
		reply.press_tick = MCPInputTriggerControl.PressTick();
		reply.release_due_s = dueS;
		if (edge == "press")
		{
			result.ok = true;
			return true;
		}
		job = new MCPJob();
		job.id = command.id;
		job.kind = "input_trigger";
		job.generation = generation;
		job.deadline_s = m_JobRunner.GetElapsedS() + INPUT_TRIGGER_JOB_SLACK_S;
		if (edge == "hold")
		{
			job.deadline_s = job.deadline_s + args.hold_s;
		}
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		job.input_trigger = reply;
		m_JobRunner.AddJob(job);
		return false;
	}

	// release ends a press this verb holds on the same entry and dik. A click
	// or hold owns its release. Anything else is not_held, which carries the
	// last release of that same key when there was one.
	protected bool ReleaseInputTriggerKey(MCPResult result, MCPInputTrigger reply, string entry, int dik)
	{
		string heldEdge;
		heldEdge = MCPInputTriggerControl.HeldEdge();
		if (heldEdge == "press" && MCPInputTriggerControl.Holds(entry, dik))
		{
			MCPInputTriggerControl.ReleaseAll("phase");
			FillInputTriggerRelease(reply);
			result.ok = true;
			return true;
		}
		if (heldEdge == "click" || heldEdge == "hold" || m_JobRunner.CountOfKind("input_trigger") > 0)
		{
			result.ok = false;
			result.error = "input_trigger_busy";
			return true;
		}
		if (MCPInputTriggerControl.LastReleaseWas(entry, dik))
		{
			FillInputTriggerRelease(reply);
		}
		result.ok = false;
		result.error = "not_held";
		return true;
	}

	protected void FillInputTriggerRelease(MCPInputTrigger reply)
	{
		reply.released_by = MCPInputTriggerControl.ReleasedBy();
		reply.release_tick = MCPInputTriggerControl.ReleaseTick();
		reply.delivered_release = MCPInputTriggerControl.ReleaseDelivered();
	}

	// kind input: resolve the UAInput, publish what was read, then refuse.
	// UAInput.Local* are getters (uainput.c:48-55) and no script setter feeds
	// them in 1.29; ForceEnable (uainput.c:83) is unmeasured and not called.
	protected bool DispatchInputTriggerInput(MCPCommand command, MCPResult result, MCPInputTrigger reply)
	{
		MCPArgs args;
		UAInputAPI api;
		UAInput input;
		TIntArray activeIds;
		int resolvedId;
		args = command.args;
		if (!IsPrintableInputName(args.name))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		reply.name = args.name;
		result.input_trigger = reply;
		api = GetUApi();
		if (!api)
		{
			result.ok = false;
			result.error = "input_api_unavailable";
			return true;
		}
		input = api.GetInputByName(args.name);
		if (!input)
		{
			result.ok = false;
			result.error = "input_unknown";
			return true;
		}
		// Index first: below 0 is the shared 1.29 placeholder (ficha 4f50).
		resolvedId = input.ID();
		reply.input_id = resolvedId;
		if (resolvedId < 0)
		{
			result.ok = false;
			result.error = "input_unknown";
			return true;
		}
		reply.exists = true;
		reply.locked = input.IsLocked();
		activeIds = new TIntArray();
		api.GetActiveInputs(activeIds);
		if (activeIds.Find(resolvedId) >= 0)
		{
			reply.in_active_inputs = true;
		}
		if (reply.locked)
		{
			result.ok = false;
			result.error = "input_locked";
			return true;
		}
		if (IsVanillaForcedInput(api, resolvedId))
		{
			result.ok = false;
			result.error = "input_denied";
			return true;
		}
		// The one place a route that drives UAInput.Local* goes once one is
		// measured in game (the design's force_enable, after its Gate 0).
		// This version has none: the answer is always input_not_drivable.
		reply.reason = "no_local_setter";
		result.ok = false;
		result.error = "input_not_drivable";
		return true;
	}

	// Inputs whose force vanilla manages: MissionGameplay forces UAWalkRunForced
	// on and off with the inventory and the map (missiongameplay.c:949-957,
	// :1008-1023) and PlayerBase suppresses UATempRaiseWeapon (playerbase.c:3019).
	// No getter reads a forced state (uainput.c:23-93), so a forcing route must
	// never touch them. Compared by input index, so the spelling cannot slip past.
	protected bool IsVanillaForcedInput(UAInputAPI api, int inputId)
	{
		UAInput walkRun;
		UAInput tempRaise;
		walkRun = api.GetInputByName("UAWalkRunForced");
		if (walkRun && walkRun.ID() == inputId)
		{
			return true;
		}
		tempRaise = api.GetInputByName("UATempRaiseWeapon");
		if (tempRaise && tempRaise.ID() == inputId)
		{
			return true;
		}
		return false;
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

	// f47b: the restore_gameplay tool re-reads camera_get as soon as this reply
	// lands and needs view=player, so while a staticcamera release is still
	// handing off to the free camera the reply is a job, posted by
	// MCP_PostJobSuccess once the handoff has finished. It counts as exclusive
	// (HasExclusiveJob), so camera_set answers busy meanwhile. True when queued.
	protected bool QueueRestoreGameplayJob(MCPCommand command, MCPResult result)
	{
		if (!m_CameraHandoffPending)
		{
			return false;
		}

		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "restore_gameplay";
		job.deadline_s = m_JobRunner.GetElapsedS() + CAMERA_JOB_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		m_JobRunner.AddJob(job);

		Log("client job queued id=" + job.id + " kind=restore_gameplay deadline_s=" + job.deadline_s);
		return true;
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
		string carError = "";
		CarScript car = ResolveOwnedCar(carError);
		if (!car)
		{
			result.ok = false;
			result.error = carError;
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
		string carError = "";
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

		car = ResolveOwnedCar(carError);
		if (!car)
		{
			result.ok = false;
			result.error = carError;
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

	protected bool IsStrictFinite(float value)
	{
		if (value != value)
		{
			return false;
		}
		if (value >= float.MAX)
		{
			return false;
		}
		if (value <= -float.MAX)
		{
			return false;
		}
		return true;
	}

	// Dead, unconscious, restrained, in a vehicle, or no weapon: fail closed.
	protected string WeaponActorError(PlayerBase player)
	{
		Weapon_Base held;
		if (!player)
		{
			return "no_player";
		}
		if (!player.IsAlive())
		{
			return "player_dead";
		}
		if (player.IsUnconscious())
		{
			return "player_unconscious";
		}
		if (player.IsRestrained())
		{
			return "player_restrained";
		}
		if (player.IsInVehicle())
		{
			return "player_in_vehicle";
		}
		held = Weapon_Base.Cast(player.GetEntityInHands());
		if (!held)
		{
			return "no_weapon_in_hands";
		}
		return "";
	}

	protected MCPJob QueueWeaponJob(MCPCommand command, MCPResult result, string verb, int generation)
	{
		MCPJob job = new MCPJob();
		job.id = command.id;
		job.kind = "weapon_action";
		job.generation = generation;
		job.sim_seen = MCPWeaponControl.SimTick();
		job.deadline_s = m_JobRunner.GetElapsedS() + WEAPON_READ_TIMEOUT_S;
		job.tick_poll_sent = result.tick_poll_sent;
		job.tick_poll_callback = result.tick_poll_callback;
		job.tick_dispatch = result.tick_dispatch;
		job.weapon_action = new MCPWeaponAction();
		job.weapon_action.verb = verb;
		m_JobRunner.AddJob(job);
		return job;
	}

	protected bool DispatchWeaponRaise(MCPCommand command, MCPResult result)
	{
		PlayerBase player;
		string actorError;
		string requestError;
		float holdTtl;
		int generation;
		MCPJob job;
		if (!m_JobRunner)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		actorError = WeaponActorError(player);
		if (actorError != "")
		{
			result.ok = false;
			result.error = actorError;
			return true;
		}
		if (!player.GetInputController())
		{
			result.ok = false;
			result.error = "no_input_controller";
			return true;
		}
		holdTtl = command.args.hold_ttl_s;
		if (!IsStrictFinite(holdTtl))
		{
			result.ok = false;
			result.error = "bad_hold_ttl_s";
			return true;
		}
		if (holdTtl <= 0.0)
		{
			result.ok = false;
			result.error = "bad_hold_ttl_s";
			return true;
		}
		if (holdTtl > MCPWeaponControl.RAISE_MAX_TTL_S)
		{
			result.ok = false;
			result.error = "bad_hold_ttl_s";
			return true;
		}
		// Multiplayer: the server holds its own copy of the override, so its
		// CanFire sees the weapon raised. Sent before the client override
		// changes: input_busy leaves both sides as they were.
		requestError = MCPWeaponControl.SendRaiseRequest(command.args.raised, holdTtl);
		if (requestError != "")
		{
			result.ok = false;
			result.error = requestError;
			return true;
		}
		if (command.args.raised)
		{
			generation = MCPWeaponControl.BeginRaise(player, holdTtl);
		}
		else
		{
			generation = MCPWeaponControl.BeginRelease(player);
			holdTtl = 0.0;
		}
		job = QueueWeaponJob(command, result, "weapon_raise", generation);
		job.weapon_action.hold_ttl_s = holdTtl;
		job.weapon_action.expires_at = MCPWeaponControl.RaiseDeadlineS();
		return false;
	}

	protected bool DispatchWeaponAim(MCPCommand command, MCPResult result)
	{
		PlayerBase player;
		string actorError;
		HumanCommandWeapons hcw;
		float dx;
		float dy;
		int generation;
		MCPJob job;
		if (!m_JobRunner)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		actorError = WeaponActorError(player);
		if (actorError != "")
		{
			result.ok = false;
			result.error = actorError;
			return true;
		}
		if (!player.GetInputController())
		{
			result.ok = false;
			result.error = "no_input_controller";
			return true;
		}
		dx = command.args.dx;
		dy = command.args.dy;
		if (!IsStrictFinite(dx))
		{
			result.ok = false;
			result.error = "bad_dx";
			return true;
		}
		if (dx > MCPWeaponControl.AIM_CHANGE_ABS_MAX)
		{
			result.ok = false;
			result.error = "bad_dx";
			return true;
		}
		if (dx < -MCPWeaponControl.AIM_CHANGE_ABS_MAX)
		{
			result.ok = false;
			result.error = "bad_dx";
			return true;
		}
		if (!IsStrictFinite(dy))
		{
			result.ok = false;
			result.error = "bad_dy";
			return true;
		}
		if (dy > MCPWeaponControl.AIM_CHANGE_ABS_MAX)
		{
			result.ok = false;
			result.error = "bad_dy";
			return true;
		}
		if (dy < -MCPWeaponControl.AIM_CHANGE_ABS_MAX)
		{
			result.ok = false;
			result.error = "bad_dy";
			return true;
		}
		hcw = player.GetCommandModifier_Weapons();
		if (!hcw)
		{
			result.ok = false;
			result.error = "aim_unreadable";
			return true;
		}
		generation = MCPWeaponControl.BeginAim(player, dx, dy);
		job = QueueWeaponJob(command, result, "weapon_aim", generation);
		job.weapon_action.aim_lr_before = hcw.GetBaseAimingAngleLR();
		job.weapon_action.aim_ud_before = hcw.GetBaseAimingAngleUD();
		return false;
	}

	protected bool DispatchWeaponFire(MCPCommand command, MCPResult result)
	{
		PlayerBase player;
		string actorError;
		int generation;
		if (!m_JobRunner)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		actorError = WeaponActorError(player);
		if (actorError != "")
		{
			result.ok = false;
			result.error = actorError;
			return true;
		}
		generation = MCPWeaponControl.BeginFire(player);
		QueueWeaponJob(command, result, "weapon_fire", generation);
		return false;
	}

	protected bool DispatchWeaponSights(MCPCommand command, MCPResult result)
	{
		PlayerBase player;
		string actorError;
		Weapon_Base held;
		ItemOptics optic;
		string mode;
		int generation;
		MCPJob job;
		if (!m_JobRunner)
		{
			result.ok = false;
			result.error = "client_not_in_game";
			return true;
		}
		if (!command.args)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		actorError = WeaponActorError(player);
		if (actorError != "")
		{
			result.ok = false;
			result.error = actorError;
			return true;
		}
		mode = command.args.mode;
		held = Weapon_Base.Cast(player.GetEntityInHands());
		// HandleADS (dayzplayerimplement.c:1970-1976). SetOptics only sets
		// m_CameraOptics (dayzplayerimplement.c:380-393); SwitchOptics enters
		// the optic (dayzplayerimplement.c:425-449). none is ExitSights
		// (dayzplayerimplement.c:396-422), which leaves both.
		if (mode == "ironsights")
		{
			if (!held.CanEnterIronsights())
			{
				result.ok = false;
				result.error = "no_ironsights";
				return true;
			}
			optic = held.GetAttachedOptics();
			player.SwitchOptics(optic, false);
			player.SetIronsights(true);
		}
		else if (mode == "optics")
		{
			optic = held.GetAttachedOptics();
			if (!optic)
			{
				result.ok = false;
				result.error = "no_optics";
				return true;
			}
			player.SetIronsights(false);
			player.SwitchOptics(optic, true);
		}
		else if (mode == "none")
		{
			player.ExitSights();
		}
		else
		{
			result.ok = false;
			result.error = "bad_mode";
			return true;
		}
		generation = MCPWeaponControl.BeginSights(player);
		job = QueueWeaponJob(command, result, "weapon_sights", generation);
		job.weapon_action.mode = mode;
		return false;
	}

	protected Transport ResolveLiveSeatedTransport(PlayerBase player)
	{
		if (!player)
		{
			return null;
		}

		Transport transport = Transport.Cast(player.GetParent());
		if (!transport)
		{
			return null;
		}

		int crewIndex = transport.CrewMemberIndex(player);
		if (crewIndex < 0)
		{
			return null;
		}

		return transport;
	}

	protected bool DispatchVehicleTelemetry(MCPCommand command, MCPResult result)
	{
		PlayerBase player;
		HumanCommandVehicle vehicleCommand;
		Transport transport;
		CarScript car;
		PlayerIdentity ownerIdentity;
		int lowBits = 0;
		int highBits = 0;

		result.ok = true;
		result.found = false;
		result.seated = false;
		result.seat = "";
		result.type = "";
		result.classname = "";

		player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			return true;
		}

		transport = ResolveLiveSeatedTransport(player);
		if (!transport)
		{
			return true;
		}

		result.found = true;
		result.seated = true;
		result.seat = "unknown";
		result.type = transport.GetType();
		result.classname = transport.ClassName();

		vehicleCommand = player.GetCommand_Vehicle();
		if (vehicleCommand && vehicleCommand.GetTransport() == transport)
		{
			result.seat = VehicleTelemetrySeatToken(vehicleCommand.GetVehicleSeat());
		}

		car = CarScript.Cast(transport);
		if (!car)
		{
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

		ownerIdentity = car.GetOwnerIdentity();
		if (ownerIdentity)
		{
			result.owner_identity = ownerIdentity.GetPlainId();
		}
		else
		{
			result.owner_identity = "";
		}

		car.GetNetworkID(lowBits, highBits);
		result.net_id_low = lowBits;
		result.net_id_high = highBits;
		result.ok = true;
		return true;
	}

	protected string VehicleTelemetrySeatToken(int vehicleSeat)
	{
		if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			return "driver";
		}
		if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_CODRIVER)
		{
			return "codriver";
		}
		if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_PASSENGER_L)
		{
			return "passenger_left";
		}
		if (vehicleSeat == DayZPlayerConstants.VEHICLESEAT_PASSENGER_R)
		{
			return "passenger_right";
		}

		return "unknown";
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
		if (args.mode != "start" && args.mode != "status" && args.mode != "stop" && args.mode != "read" && args.mode != "clear" && args.mode != "dump")
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
		else if (args.mode == "dump")
		{
			if (!MCPVehicleTrace.Dump(args.trace_id))
			{
				result.ok = false;
				result.error = MCPVehicleTrace.GetLastError();
				return true;
			}
			result.trace = MCPVehicleTrace.View("dump", args.trace_id, 0, 1);
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

	// Client-only timeline of the local player's action and the item in hands
	// (ficha 5535). Sampling itself runs in OnTick via MCPAnimTimeline.Tick.
	protected bool DispatchAnimTimeline(MCPCommand command, MCPResult result)
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
		if (!IsValidTraceId(args.trace_id) || args.cursor < 0 || args.limit < 1 || args.limit > MCPAnimTimeline.LIMIT_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (args.sample_hz < MCPAnimTimeline.SAMPLE_HZ_MIN || args.sample_hz > MCPAnimTimeline.SAMPLE_HZ_MAX || args.max_samples < MCPAnimTimeline.MAX_SAMPLES_MIN || args.max_samples > MCPAnimTimeline.MAX_SAMPLES_MAX)
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}
		if (!MCPAnimTimeline.SourcesOk(args.sources))
		{
			result.ok = false;
			result.error = "bad_args";
			return true;
		}

		if (args.mode == "start")
		{
			if (!MCPAnimTimeline.Start(args.trace_id, args.sample_hz, args.max_samples, args.sources))
			{
				result.ok = false;
				result.error = MCPAnimTimeline.GetLastError();
				return true;
			}

			result.timeline = MCPAnimTimeline.View("start", args.trace_id, 0, 1);
			result.ok = result.timeline != null;
			if (!result.ok)
			{
				result.error = MCPAnimTimeline.GetLastError();
			}
			return true;
		}

		if (!MCPAnimTimeline.Matches(args.trace_id))
		{
			result.ok = false;
			result.error = "trace_not_found";
			return true;
		}

		if (args.mode == "stop")
		{
			if (!MCPAnimTimeline.Stop(args.trace_id))
			{
				result.ok = false;
				result.error = MCPAnimTimeline.GetLastError();
				return true;
			}
			result.timeline = MCPAnimTimeline.View("stop", args.trace_id, 0, 1);
		}
		else if (args.mode == "clear")
		{
			// The header is read before the wipe: it is the last view of the trace.
			MCPAnimTimelineRead clearView = MCPAnimTimeline.View("clear", args.trace_id, 0, 1);
			if (!clearView || !MCPAnimTimeline.Clear(args.trace_id))
			{
				result.ok = false;
				result.error = MCPAnimTimeline.GetLastError();
				return true;
			}
			result.timeline = clearView;
		}
		else
		{
			result.timeline = MCPAnimTimeline.View(args.mode, args.trace_id, args.cursor, args.limit);
		}

		if (!result.timeline)
		{
			result.ok = false;
			result.error = MCPAnimTimeline.GetLastError();
			return true;
		}
		result.ok = true;
		return true;
	}

	protected bool DispatchUiTree(MCPCommand command, MCPResult result)
	{
		MCPArgs args = command.args;
		BeginUiRequest(args, result);
		string error = "";
		Widget root = ResolveUiRoot(args, error);
		if (!root)
		{
			result.ok = false;
			result.error = error;
			return true;
		}
		// The active-menu legacy (root and path both empty) is not a name match,
		// so it carries no matched_path.
		if (UiRequestNamesTarget(args))
		{
			FillUiMatchedPath(root, result);
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
		BeginUiRequest(command.args, result);
		if (command.args)
		{
			// Only set-text echoes the text, and it echoes "" too: an empty
			// write is a valid request, not an absent field.
			result.ui_request.requested_text = command.args.text;
		}
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
		FillUiMatchedPath(target, result);

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

	//! ui_click(path, root?, button, mode, bubble). Resolution comes first and is
	//! shared with the other UI verbs: an absent or ambiguous target returns
	//! before any handler runs. mode is normalised only after the unique match:
	//! "" is direct, and direct keeps the legacy lookup up to the first handler
	//! and its return. `complete` hands the resolved target to
	//! DispatchUiClickComplete (down->up->click at the measured centre), the only
	//! reader of `bubble`, so direct never reads it.
	protected bool DispatchUiClick(MCPCommand command, MCPResult result)
	{
		BeginUiRequest(command.args, result);
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
		FillUiMatchedPath(target, result);

		string mode = command.args.mode;
		if (mode == "")
		{
			mode = "direct";
		}
		if (mode == "complete")
		{
			return DispatchUiClickComplete(command, result, target, mouseButton);
		}
		if (mode != "direct")
		{
			result.ok = false;
			result.error = "bad_args";
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

	//! ui_click mode="complete" on the target DispatchUiClick resolved. The
	//! centre of its screen box (GetScreenPos/GetScreenSize,
	//! 1_core\proto\enwidgets.c:153-154), rounded to whole pixels, is the x, y
	//! of all three phases. OnMouseButtonDown, OnMouseButtonUp and OnClick then
	//! run in that order, each through the direct-mode walk (InvokeUiHandler)
	//! and each whatever the phases before it returned, because a real click
	//! delivers all three; bubble only decides whether a phase a handler
	//! declined moves on up the chain. This synthesizes handler calls in
	//! script: no OS input, no cursor move, no focus, no hit test, and
	//! engine-side widget state that only a real mouse drives (a button's
	//! pressed look) does not change. handler and clicked describe the click
	//! phase, as in direct mode. ok means a phase was consumed; otherwise
	//! no_handler means no phase found a handler and not_handled that handlers
	//! ran and none consumed.
	protected bool DispatchUiClickComplete(MCPCommand command, MCPResult result, Widget target, int mouseButton)
	{
		float screenX;
		float screenY;
		float screenW;
		float screenH;
		target.GetScreenPos(screenX, screenY);
		target.GetScreenSize(screenW, screenH);
		int centerX = Math.Round(screenX + screenW / 2.0);
		int centerY = Math.Round(screenY + screenH / 2.0);

		MCPUiClickSequence sequence = new MCPUiClickSequence();
		sequence.x = centerX;
		sequence.y = centerY;
		sequence.bubble = command.args.bubble;
		result.click_sequence = sequence;
		// Read before the phases run: a phase handler may unlink the target.
		result.user_id = target.GetUserID();

		MCPUiClickPhase down = RunUiClickPhase(target, "down", "OnMouseButtonDown", mouseButton, sequence);
		MCPUiClickPhase up = RunUiClickPhase(target, "up", "OnMouseButtonUp", mouseButton, sequence);
		MCPUiClickPhase click = RunUiClickPhase(target, "click", "OnClick", mouseButton, sequence);

		result.handler = click.handler;
		result.clicked = click.consumed;
		if (down.consumed || up.consumed || click.consumed)
		{
			result.ok = true;
			return true;
		}

		result.ok = false;
		if (down.received == 0 && up.received == 0 && click.received == 0)
		{
			result.error = "no_handler";
		}
		else
		{
			result.error = "not_handled";
		}
		return true;
	}

	//! One phase of a complete click: a fresh walk from the target at the
	//! sequence's centre and bubble setting. The phase joins the reply in the
	//! order the phases run.
	protected MCPUiClickPhase RunUiClickPhase(Widget target, string phaseName, string method, int mouseButton, MCPUiClickSequence sequence)
	{
		MCPUiClickPhase phase = new MCPUiClickPhase();
		phase.phase = phaseName;
		sequence.phases.Insert(phase);
		InvokeUiHandler(target, method, sequence.x, sequence.y, mouseButton, sequence.bubble, phase);
		return phase;
	}

	//! Hot UI iteration: rebuild a standalone preview root from a .layout on disk
	//! and report the engine rects of the resulting tree. $profile: is the only
	//! prefix the engine re-reads without a repack; an addon-prefixed path is
	//! served by the PBO and never sees a loose file (measured 2026-08-19).
	//! mode="close" unlinks the preview and loads nothing. The request echo
	//! carries the literal path on load and "" on close; `source` carries no UI
	//! meaning.
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
			result.ui_request = new MCPUiRequestEcho();
			result.ui_request.requested_path = "";
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
		result.ui_request = new MCPUiRequestEcho();
		result.ui_request.requested_path = args.path;
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

		BeginUiRequest(command.args, result);
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
		FillUiMatchedPath(target, result);

		topmost = target;
		parent = topmost.GetParent();
		while (parent)
		{
			topmost = parent;
			parent = topmost.GetParent();
		}

		activeOk = SetActiveWindow(topmost, false);
		SetFocus(target);

		// The holder is compared by handle only. No name or path of the widget
		// that actually holds focus is serialised: ui_request.matched_path
		// identifies the target, and the holder's identity stays private.
		focused = GetFocus();
		result.ok = (focused == target);

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
		Log("ui_focus path=" + args.path + " active=" + activeStr + " ok=" + okStr);
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

		string targetMode;
		if (command.cmd == "action_use_target")
		{
			targetMode = command.args.target;
			if (targetMode != "hands" && targetMode != "self")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}
		}
		else
		{
			if (command.args.target != "" && command.args.target != "world")
			{
				result.ok = false;
				result.error = "bad_args";
				return true;
			}

			targetMode = "world";
		}

		result.target = targetMode;

		ActionTarget actionTarget;
		if (targetMode == "hands")
		{
			ItemBase targetItem = player.GetItemInHands();
			if (!targetItem)
			{
				result.ok = false;
				result.error = "no_item_in_hands";
				return true;
			}

			if (command.args.classname != "" && targetItem.GetType() != command.args.classname)
			{
				result.ok = false;
				result.error = "held_item_mismatch";
				return true;
			}

			ItemBase targetParent = ItemBase.Cast(targetItem.GetHierarchyParent());
			actionTarget = new ActionTarget(targetItem, targetParent, -1, vector.Zero, -1);
			result.classname = targetItem.GetType();
			result.pos_real = new array<float>();
			VectorToArray(targetItem.GetPosition(), result.pos_real);
			result.distance = vector.Distance(player.GetPosition(), targetItem.GetPosition());
		}
		else if (targetMode == "self")
		{
			actionTarget = new ActionTarget(null, null, -1, vector.Zero, -1);
			result.classname = "";
			result.pos_real = new array<float>();
			VectorToArray(player.GetPosition(), result.pos_real);
			result.distance = 0.0;
		}
		else
		{
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
			// action_use_door replaces the building centre with the component
			// point IsInReach measures (actionbase.c:1196-1206).
			vector cursorHitPos = targetObj.GetPosition();
			if (command.cmd == "action_use_door")
			{
				// Echo the requested index before any refusal. The int defaults
				// to 0, which is a real door, so an early return would otherwise
				// look like door 0 to the caller.
				int wantedDoor = command.args.door_index;
				result.door_index = wantedDoor;

				Building doorBuilding = Building.Cast(targetObj);
				if (!doorBuilding)
				{
					result.ok = false;
					result.error = "not_a_building";
					return true;
				}

				int doorCount = doorBuilding.GetDoorCount();
				if (wantedDoor < 0 || wantedDoor >= doorCount)
				{
					result.ok = false;
					result.error = "door_out_of_range";
					return true;
				}

				int doorComponent = -1;
				int componentScan = 0;
				while (componentScan < ACTION_USE_DOOR_COMPONENT_CAP && doorComponent < 0)
				{
					if (doorBuilding.GetDoorIndex(componentScan) == wantedDoor)
					{
						doorComponent = componentScan;
					}

					componentScan = componentScan + 1;
				}

				if (doorComponent < 0)
				{
					result.ok = false;
					result.error = "door_component_not_found";
					return true;
				}

				array<string> doorComponentNames = new array<string>();
				doorBuilding.GetActionComponentNameList(doorComponent, doorComponentNames);
				bool doorSelectionFound = false;
				string doorSelection = "";
				int nameScan = 0;
				string doorComponentName = "";
				while (nameScan < doorComponentNames.Count() && !doorSelectionFound)
				{
					doorComponentName = doorComponentNames.Get(nameScan);
					if (doorComponentName.Contains("doorstwin"))
					{
						nameScan = nameScan + 1;
					}
					else
					{
						doorSelection = doorComponentName;
						doorSelectionFound = true;
					}
				}

				if (!doorSelectionFound)
				{
					result.ok = false;
					result.error = "door_component_not_found";
					return true;
				}

				vector doorModelPos = doorBuilding.GetSelectionPositionMS(doorSelection);
				cursorHitPos = doorBuilding.ModelToWorld(doorModelPos);
				actionTarget = new ActionTarget(doorBuilding, null, doorComponent, cursorHitPos, 0);
				result.component_index = doorComponent;
			}
			else
			{
				actionTarget = new ActionTarget(targetObj, null, -1, cursorHitPos, 0);
			}

			result.classname = targetObj.GetType();
			result.pos_real = new array<float>();
			VectorToArray(cursorHitPos, result.pos_real);
			result.distance = vector.Distance(player.GetPosition(), cursorHitPos);
		}

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

	//! Common UI resolver (DAG v6 UI contract). `root` names a widget that must
	//! be unique in the whole workspace; `path` is then a name resolved inside
	//! that scope. Without `root`, `path` is resolved over the whole workspace,
	//! ScriptView roots included: 0 matches is widget_not_found, 2 or more is
	//! ambiguous_path, and the first homonym is never chosen. Only a request with
	//! both empty keeps the active-menu legacy (ui_tree).
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

		string rootName = "";
		string pathName = "";
		if (args)
		{
			rootName = args.root;
			pathName = args.path;
		}

		Widget scope = workspace;
		if (rootName != "")
		{
			string rootError = "";
			scope = ResolveUniqueUiWidget(workspace, rootName, rootError);
			if (!scope)
			{
				error = rootError;
				return null;
			}
			if (pathName == "")
			{
				return scope;
			}
		}

		if (pathName != "")
		{
			string pathError = "";
			Widget named = ResolveUniqueUiWidget(scope, pathName, pathError);
			if (!named)
			{
				error = pathError;
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

	//! Resolve `name` to exactly one widget under `scope` (scope included).
	//! Zero is widget_not_found, two or more is ambiguous_path; only a single
	//! match is returned, so a homonym can never be picked by walk order.
	protected Widget ResolveUniqueUiWidget(Widget scope, string name, out string error)
	{
		MCPUiNameMatch match = new MCPUiNameMatch();
		CountUiWidgetsNamed(scope, name, match);
		if (match.count == 0)
		{
			error = "widget_not_found";
			return null;
		}
		if (match.count > 1)
		{
			error = "ambiguous_path";
			return null;
		}
		error = "";
		return match.first;
	}

	//! Depth-first count of widgets named `name` under `scope`, scope included.
	//! Stops as soon as two are seen: the resolver only needs 0, 1 or more.
	protected void CountUiWidgetsNamed(Widget scope, string name, MCPUiNameMatch match)
	{
		if (!scope || !match)
		{
			return;
		}
		if (match.count >= 2)
		{
			return;
		}

		if (scope.GetName() == name)
		{
			match.count = match.count + 1;
			if (match.count == 1)
			{
				match.first = scope;
			}
			if (match.count >= 2)
			{
				return;
			}
		}

		Widget child = scope.GetChildren();
		while (child)
		{
			CountUiWidgetsNamed(child, name, match);
			if (match.count >= 2)
			{
				return;
			}
			child = child.GetSibling();
		}
	}

	protected bool UiRequestNamesTarget(MCPArgs args)
	{
		if (!args)
		{
			return false;
		}
		if (args.root != "")
		{
			return true;
		}
		return args.path != "";
	}

	//! Request echo for the core UI verbs, created before anything is resolved
	//! so a verb that fails on its arguments still answers with the envelope:
	//! an empty one is a visible defect, not noise. requested_path and
	//! requested_root are the literal inputs. matched_path is filled only by
	//! FillUiMatchedPath after a unique match and is never copied from the
	//! request; requested_text is set by ui_set_text alone.
	protected void BeginUiRequest(MCPArgs args, MCPResult result)
	{
		if (!result)
		{
			return;
		}
		result.ui_request = new MCPUiRequestEcho();
		if (args)
		{
			result.ui_request.requested_path = args.path;
			result.ui_request.requested_root = args.root;
		}
	}

	protected void FillUiMatchedPath(Widget target, MCPResult result)
	{
		if (!target || !result || !result.ui_request || !GetGame())
		{
			return;
		}
		WorkspaceWidget workspace = GetGame().GetWorkspace();
		if (!workspace)
		{
			return;
		}
		result.ui_request.matched_path = BuildUiMatchedPath(target, workspace);
	}

	//! Canonical identity of a resolved widget (DAG v6): a root-to-leaf walk over
	//! GetParent(), one "/<pct-name>@<ordinal>" segment per node, the name
	//! percent-encoded byte by byte with uppercase hex outside RFC3986 unreserved,
	//! the ordinal 0-based among siblings whose GetName() is byte-identical. The
	//! first segment is always the live workspace name at @0, whatever root the
	//! caller gave. A segment or ordinal the live handles cannot account for
	//! yields "" (no identity) rather than a guessed path.
	protected string BuildUiMatchedPath(Widget target, WorkspaceWidget workspace)
	{
		string path = "";
		string segment = "";
		if (!target || !workspace)
		{
			return "";
		}

		Widget cursor = target;
		while (cursor && cursor != workspace)
		{
			int ordinal = UiSiblingOrdinal(cursor, workspace);
			if (ordinal < 0)
			{
				return "";
			}
			if (!EncodeUiPathSegment(cursor.GetName(), segment))
			{
				return "";
			}
			path = "/" + segment + "@" + ordinal.ToString() + path;
			cursor = cursor.GetParent();
		}

		if (!EncodeUiPathSegment(workspace.GetName(), segment))
		{
			return "";
		}
		return "/" + segment + "@0" + path;
	}

	//! 0-based ordinal of `w` among the children of its parent that share its
	//! byte-exact name. A top-level root that reports no parent has its siblings
	//! read from the workspace. -1 when `w` is not found among them.
	protected int UiSiblingOrdinal(Widget w, WorkspaceWidget workspace)
	{
		if (!w)
		{
			return -1;
		}
		Widget parent = w.GetParent();
		if (!parent)
		{
			parent = workspace;
		}
		if (!parent || parent == w)
		{
			return -1;
		}

		string name = w.GetName();
		int ordinal = 0;
		Widget sibling = parent.GetChildren();
		while (sibling)
		{
			if (sibling == w)
			{
				return ordinal;
			}
			if (sibling.GetName() == name)
			{
				ordinal = ordinal + 1;
			}
			sibling = sibling.GetSibling();
		}

		return -1;
	}

	//! Percent-encode one path segment byte by byte. string.Length and Substring
	//! are byte-based (1_core\proto\enstring.c:199,113; the character forms are
	//! LengthUtf8/SubstringUtf8). Unreserved is RFC3986 ALPHA / DIGIT / "-" "."
	//! "_" "~", so "%", "/" and "@" are always encoded; hex is uppercase. A
	//! negative ToAscii is a sign-extended high byte. A code still outside 0..255
	//! is not a byte: the call returns false and no path is published.
	protected bool EncodeUiPathSegment(string value, out string encoded)
	{
		string hexDigits = "0123456789ABCDEF";
		string character = "";
		int code = 0;
		int highNibble = 0;
		int lowNibble = 0;
		int i = 0;
		encoded = "";
		while (i < value.Length())
		{
			character = value.Substring(i, 1);
			code = character.ToAscii();
			if (code < 0)
			{
				code = code + 256;
			}
			if (code < 0 || code > 255)
			{
				encoded = "";
				return false;
			}
			if (IsUiPathUnreserved(code))
			{
				encoded = encoded + character;
			}
			else
			{
				highNibble = code / 16;
				lowNibble = code - (highNibble * 16);
				encoded = encoded + "%" + hexDigits.Substring(highNibble, 1) + hexDigits.Substring(lowNibble, 1);
			}
			i = i + 1;
		}
		return true;
	}

	protected bool IsUiPathUnreserved(int code)
	{
		if (code >= 65 && code <= 90)
		{
			return true;
		}
		if (code >= 97 && code <= 122)
		{
			return true;
		}
		if (code >= 48 && code <= 57)
		{
			return true;
		}
		if (code == 45 || code == 46 || code == 95 || code == 126)
		{
			return true;
		}
		return false;
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

	//! Direct mode's lookup: the shared walk with OnClick at (0, 0) and no
	//! bubbling, so the first handler found ends it whatever it returns. Returns
	//! whether that handler CONSUMED the click, not whether one was found:
	//! ScriptedWidgetEventHandler.OnClick reports that in its return value
	//! (1_core\proto\enwidgets.c:658). handlerName names the handler found and
	//! stays empty when there was none, so the caller can tell an absent handler
	//! from a declining one.
	protected bool InvokeUiClick(Widget target, int mouseButton, out string handlerName)
	{
		MCPUiClickPhase click = new MCPUiClickPhase();
		InvokeUiHandler(target, "OnClick", 0, 0, mouseButton, false, click);
		handlerName = click.handler;
		return click.consumed;
	}

	//! The handler walk of ui_click, one event per call. From target up through
	//! its parents, each widget offers its script handler (GetScript), then its
	//! user-data handler (GetUserData); the active menu comes last. Each one is
	//! handed `method` (OnClick, OnMouseButtonDown or OnMouseButtonUp) with
	//! (target, x, y, mouseButton) by DeliverUiEvent or DeliverUiMenuEvent.
	//! Without bubble the first handler that receives the event ends the walk
	//! whatever it returns; with bubble one that returns false passes it on to
	//! the next, and the first that returns true ends it. Every receiver is
	//! booked in `walk` (NoteUiHandler).
	protected void InvokeUiHandler(Widget target, string method, int x, int y, int mouseButton, bool bubble, MCPUiClickPhase walk)
	{
		if (!GetGame())
		{
			return;
		}
		// An earlier phase may have unlinked the target: this one has no receiver.
		if (!target)
		{
			return;
		}

		bool consumed = false;
		Widget cursor = target;
		while (cursor)
		{
			Class scriptInst;
			cursor.GetScript(scriptInst);
			if (scriptInst)
			{
				string scriptName = scriptInst.ClassName();
				if (DeliverUiEvent(scriptInst, method, target, x, y, mouseButton, consumed))
				{
					NoteUiHandler(walk, scriptName, consumed);
					if (consumed || !bubble)
					{
						return;
					}
				}
			}
			// A handler that declined may have unlinked the target. cursor is the
			// target or one of its ancestors, so it cannot outlive the target.
			if (!target)
			{
				return;
			}

			Class userInst;
			cursor.GetUserData(userInst);
			if (userInst)
			{
				string userName = userInst.ClassName();
				if (DeliverUiEvent(userInst, method, target, x, y, mouseButton, consumed))
				{
					NoteUiHandler(walk, userName, consumed);
					if (consumed || !bubble)
					{
						return;
					}
				}
			}
			if (!target)
			{
				return;
			}

			cursor = cursor.GetParent();
		}

		UIManager ui = GetGame().GetUIManager();
		if (ui)
		{
			UIScriptedMenu menu = ui.GetMenu();
			if (menu)
			{
				string menuName = menu.ClassName();
				if (DeliverUiMenuEvent(menu, method, target, x, y, mouseButton, consumed))
				{
					NoteUiHandler(walk, menuName, consumed);
				}
			}
		}
	}

	//! Hands one walk candidate the event. A ScriptedWidgetEventHandler gets the
	//! typed call (1_core\proto\enwidgets.c:658,668-669); any other instance, a
	//! Dabs ScriptView for one, gets CallFunctionParams with the same method and
	//! arguments. Returns false when the instance has no such method, which the
	//! walk treats as an empty slot; consumed is the handler's return.
	protected bool DeliverUiEvent(Class inst, string method, Widget target, int x, int y, int mouseButton, out bool consumed)
	{
		consumed = false;
		ScriptedWidgetEventHandler handler = ScriptedWidgetEventHandler.Cast(inst);
		if (handler)
		{
			if (method == "OnMouseButtonDown")
			{
				consumed = handler.OnMouseButtonDown(target, x, y, mouseButton);
				return true;
			}
			if (method == "OnMouseButtonUp")
			{
				consumed = handler.OnMouseButtonUp(target, x, y, mouseButton);
				return true;
			}
			if (method == "OnClick")
			{
				consumed = handler.OnClick(target, x, y, mouseButton);
				return true;
			}
			return false;
		}

		bool reflected = false;
		int called = g_Game.GameScript.CallFunctionParams(inst, method, reflected, new Param4<Widget, int, int, int>(target, x, y, mouseButton));
		if (!called)
		{
			return false;
		}

		consumed = reflected;
		return true;
	}

	//! The active menu's turn. UIScriptedMenu is not a
	//! ScriptedWidgetEventHandler: it declares the three events itself
	//! (3_game\tools\uiscriptedmenu.c:238,373,388) and gets the typed call.
	protected bool DeliverUiMenuEvent(UIScriptedMenu menu, string method, Widget target, int x, int y, int mouseButton, out bool consumed)
	{
		consumed = false;
		if (method == "OnMouseButtonDown")
		{
			consumed = menu.OnMouseButtonDown(target, x, y, mouseButton);
			return true;
		}
		if (method == "OnMouseButtonUp")
		{
			consumed = menu.OnMouseButtonUp(target, x, y, mouseButton);
			return true;
		}
		if (method == "OnClick")
		{
			consumed = menu.OnClick(target, x, y, mouseButton);
			return true;
		}
		return false;
	}

	//! Books one receiver of a walk. The first one names the event until one
	//! consumes it, so handler is the consumer when there is one; received
	//! counts them.
	protected void NoteUiHandler(MCPUiClickPhase walk, string handlerName, bool consumed)
	{
		walk.received = walk.received + 1;
		if (walk.received == 1 || consumed)
		{
			walk.handler = handlerName;
		}
		if (consumed)
		{
			walk.consumed = true;
		}
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
		else if (job.kind == "weapon_action")
		{
			return ProcessWeaponActionJob(job);
		}
		else if (job.kind == "restore_gameplay")
		{
			// Done once OnTick has finished the camera handoff (TickCameraHandoff).
			return !m_CameraHandoffPending;
		}
		else if (job.kind == "input_trigger")
		{
			return ProcessInputTriggerJob(job);
		}

		return false;
	}

	// A click or hold answers once its key is released. MaintainFromTick runs
	// before the job runner in OnTick, so the answer leaves on the release
	// tick. A release by anything but the phase itself is aborted.
	protected bool ProcessInputTriggerJob(MCPJob job)
	{
		if (!job.input_trigger)
		{
			job.error = "bad_args";
			return true;
		}
		if (!MCPInputTriggerControl.WasReleased(job.generation))
		{
			return false;
		}
		FillInputTriggerRelease(job.input_trigger);
		if (job.input_trigger.released_by != "phase")
		{
			job.error = "aborted";
		}
		return true;
	}

	// Posted one command tick after the override, so the numbers are what
	// the engine read, not what this call asked for.
	protected bool ProcessWeaponActionJob(MCPJob job)
	{
		string verb;
		string abort;
		PlayerBase player;
		HumanInputController hic;
		HumanCommandWeapons hcw;
		vector change;
		if (!job.weapon_action)
		{
			job.error = "bad_args";
			return true;
		}
		verb = job.weapon_action.verb;
		if (job.generation != MCPWeaponControl.Generation(verb))
		{
			abort = MCPWeaponControl.Abort(verb);
			if (abort == "")
			{
				abort = "superseded";
			}
			job.error = abort;
			return true;
		}
		if (MCPWeaponControl.SimTick() <= job.sim_seen)
		{
			return false;
		}
		player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			job.error = "no_player";
			return true;
		}
		if (verb == "weapon_raise")
		{
			job.weapon_action.raised = player.IsRaised();
			hic = player.GetInputController();
			if (hic)
			{
				job.weapon_action.input_raised = hic.IsWeaponRaised();
			}
			job.weapon_action.expires_at = MCPWeaponControl.RaiseDeadlineS();
			return true;
		}
		if (verb == "weapon_aim")
		{
			hcw = player.GetCommandModifier_Weapons();
			hic = player.GetInputController();
			if (!hcw)
			{
				job.error = "aim_unreadable";
				return true;
			}
			if (!hic)
			{
				job.error = "aim_unreadable";
				return true;
			}
			job.weapon_action.aim_lr_after = hcw.GetBaseAimingAngleLR();
			job.weapon_action.aim_ud_after = hcw.GetBaseAimingAngleUD();
			change = hic.GetAimChange();
			job.weapon_action.aim_change_0 = change[0];
			job.weapon_action.aim_change_1 = change[1];
			job.weapon_action.aim_change_2 = change[2];
			return true;
		}
		if (verb == "weapon_fire")
		{
			job.weapon_action.accepted = MCPWeaponControl.FireAccepted();
			job.weapon_action.reason = MCPWeaponControl.FireReason();
			return true;
		}
		if (verb == "weapon_sights")
		{
			job.weapon_action.ironsights = player.IsInIronsights();
			job.weapon_action.optics = player.IsInOptics();
			return true;
		}
		job.error = "unknown_command";
		return true;
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
				RestoreGameplay();
				return true;
			}

			job.sample_start_s = m_JobRunner.GetElapsedS();
			job.phase = CAMERA_PHASE_SETTLE;
			return false;
		}

		if (job.phase == CAMERA_PHASE_SETTLE)
		{
			// Time-only settle. Camera.IsInterpolationComplete / GetCurrentFOV
			// walk the same native camera object as GetCurrentCamera (SUB_BRZ
			// 2026-09-08) and have frozen the client render after camera_set
			// (f47b). REPORT still snapshots m_ActiveCam or the player view.
			// Seated apply/observe still needs the wall-time settle: the generic
			// CameraReadError reject is not a settle abort (Sol APROBAR_CONTRATO).
			if (CameraReadError() != "")
			{
				PlayerBase settlePlayer = PlayerBase.Cast(GetGame().GetPlayer());
				if (!ResolveLiveSeatedTransport(settlePlayer))
				{
					job.phase = CAMERA_PHASE_REPORT;
					return true;
				}
			}
			float elapsed = m_JobRunner.GetElapsedS() - job.sample_start_s;
			if (elapsed >= job.sample_s_target)
			{
				job.phase = CAMERA_PHASE_REPORT;
				return true;
			}

			return false;
		}

		return true;
	}

	protected Transport FindTransportNearClient(vector pos)
	{
		m_ReadyObjects.Clear();
		m_ReadyProxyCargos.Clear();
		GetGame().GetObjectsAtPosition3D(pos, DRIVE_CLIENT_SEARCH_RADIUS, m_ReadyObjects, m_ReadyProxyCargos);

		Transport best;
		float bestDist = 0.0;
		int i = 0;
		while (i < m_ReadyObjects.Count())
		{
			Object found = m_ReadyObjects.Get(i);
			Transport vehicle = Transport.Cast(found);
			if (vehicle)
			{
				float distSq = vector.DistanceSq(pos, vehicle.GetPosition());
				if (!best)
				{
					best = vehicle;
					bestDist = distSq;
				}
				else if (distSq < bestDist)
				{
					best = vehicle;
					bestDist = distSq;
				}
			}

			i = i + 1;
		}

		return best;
	}

	// fb-20260822-191204-1b40: engine_set and vehicle_control act only on a car
	// the local player drives and whose simulation this client owns, as
	// vehicle_trace start requires. Seat: HumanCommandVehicle.GetVehicleSeat
	// (human.c:696); vehicle_get_in_client already demands VEHICLESEAT_DRIVER
	// for crew position 0 of a car. Owner: Pawn.IsOwner, true when "simulated by
	// the owner" (pawn.c:193-194); vanilla hands a car to its driver's identity
	// in PlayerBase.OnVehicleSeatDriverEnter (playerbase.c:4266-4281). On a
	// refusal it returns null and error names the first check that failed:
	// not_seated (no player, no vehicle command, or not a CarScript, as before),
	// then not_driver, then not_owner.
	protected CarScript ResolveOwnedCar(out string error)
	{
		error = "";
		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (!player)
		{
			error = "not_seated";
			return null;
		}

		HumanCommandVehicle vehicleCommand = player.GetCommand_Vehicle();
		if (!vehicleCommand)
		{
			error = "not_seated";
			return null;
		}

		CarScript car = CarScript.Cast(vehicleCommand.GetTransport());
		if (!car)
		{
			error = "not_seated";
			return null;
		}

		if (vehicleCommand.GetVehicleSeat() != DayZPlayerConstants.VEHICLESEAT_DRIVER)
		{
			error = "not_driver";
			return null;
		}

		if (!car.IsOwner())
		{
			error = "not_owner";
			return null;
		}

		return car;
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

		// A release still handing off would share the free camera and the player
		// simulation with this apply: finish it first (f47b). No-op otherwise.
		FinishCameraHandoff();
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
			ObserveSeatedCameraApply(result.camera, job.args);
			if (result.camera && result.camera.error == "camera_unmoved_cabin")
			{
				result.ok = false;
				result.error = "camera_unmoved_cabin";
			}
			PostResult(result);
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

		if (job.kind == "weapon_action")
		{
			PostWeaponActionJob(job, "");
		}

		// Exactly the reply the restore_gameplay dispatch posts when no handoff runs.
		if (job.kind == "restore_gameplay")
		{
			MCPResult resultRestore = new MCPResult();
			resultRestore.id = job.id;
			resultRestore.ok = true;
			resultRestore.tick_poll_sent = job.tick_poll_sent;
			resultRestore.tick_poll_callback = job.tick_poll_callback;
			resultRestore.tick_dispatch = job.tick_dispatch;
			PostResult(resultRestore);
		}

		if (job.kind == "input_trigger")
		{
			PostInputTriggerJob(job);
		}
	}

	override void MCP_PostJobFailure(MCPJob job)
	{
		if (job && job.kind == "weapon_action")
		{
			PostWeaponActionJob(job, "");
			return;
		}

		if (job && job.kind == "input_trigger")
		{
			PostInputTriggerJob(job);
			return;
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

		if (job.kind == "weapon_action")
		{
			PostWeaponActionJob(job, "weapon_read_timeout");
			return;
		}

		if (job.kind == "input_trigger")
		{
			PostInputTriggerTimeout(job);
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

	// GetCurrentCamera crashes inside the native getter before it can return
	// null (SUB_BRZ RPTs 2026-09-08, deployed BuildCameraResult:3664).
	// A local player does not prove that the native scripted camera exists.
	// Inspect only our retained instance; absence is not proof of player view.
	protected string CameraReadError()
	{
		if (!IsClientInGame())
		{
			return "client_not_in_game";
		}

		PlayerBase cameraPlayer = PlayerBase.Cast(GetGame().GetPlayer());
		if (!cameraPlayer)
		{
			return "camera_unavailable_player";
		}

		// Generic scripted-camera reject while seated. Presence is live parent
		// Transport + CrewMemberIndex, never GetCommand_Vehicle. BuildCameraResult
		// takes the seated apply/observe branch before treating this as final.
		if (ResolveLiveSeatedTransport(cameraPlayer))
		{
			return "camera_unavailable_vehicle";
		}
		if (cameraPlayer.GetParent())
		{
			return "camera_unavailable_parented_player";
		}

		if (!m_ActiveCam)
		{
			return "camera_unavailable_no_scripted_camera";
		}
		if (!m_ActiveCam.IsActive())
		{
			return "camera_unavailable_inactive";
		}

		return "";
	}

	protected bool CameraErrorIsMissingScripted(string cameraError)
	{
		if (cameraError == "camera_unavailable_no_scripted_camera")
		{
			return true;
		}

		if (cameraError == "camera_unavailable_inactive")
		{
			return true;
		}

		return false;
	}

	protected bool IsNonZeroFiniteVector(vector value)
	{
		if (!IsFiniteFloat(value[0]) || !IsFiniteFloat(value[1]) || !IsFiniteFloat(value[2]))
		{
			return false;
		}

		if (value[0] == 0.0 && value[1] == 0.0 && value[2] == 0.0)
		{
			return false;
		}

		return true;
	}

	// Positive player-view read. DayZPlayer.GetCurrentCameraTransform is a
	// different native from Camera.GetCurrentCamera (the SUB_BRZ crash). A
	// finite non-zero direction is the liberated discriminator; empty or
	// non-finite vectors stay illegible. Never infer player view from the
	// mere absence of m_ActiveCam (0d65 / decision 6).
	protected bool FillPlayerCameraView(MCPCamera camera, PlayerBase cameraPlayer)
	{
		if (!camera || !cameraPlayer)
		{
			return false;
		}

		vector playerPos;
		vector playerDir;
		vector playerRot;
		cameraPlayer.GetCurrentCameraTransform(playerPos, playerDir, playerRot);
		if (!IsFiniteFloat(playerPos[0]) || !IsFiniteFloat(playerPos[1]) || !IsFiniteFloat(playerPos[2]))
		{
			return false;
		}

		if (!IsNonZeroFiniteVector(playerDir))
		{
			return false;
		}

		VectorToArray(playerPos, camera.pos);
		VectorToArray(playerDir, camera.dir);
		camera.ok = true;
		camera.viewport_moved = false;
		camera.view = "player";
		camera.error = "";
		return true;
	}

	// Observe-only seated view. Parent+crew is already established by
	// CameraReadError → camera_unavailable_vehicle. GetCommand_Vehicle is
	// never presence. A readable transform is view=vehicle, not scripted.
	protected bool FillSeatedCameraView(MCPCamera camera, PlayerBase cameraPlayer)
	{
		if (!camera || !cameraPlayer)
		{
			return false;
		}

		if (!ResolveLiveSeatedTransport(cameraPlayer))
		{
			return false;
		}

		vector playerPos;
		vector playerDir;
		vector playerRot;
		cameraPlayer.GetCurrentCameraTransform(playerPos, playerDir, playerRot);
		if (!IsFiniteFloat(playerPos[0]) || !IsFiniteFloat(playerPos[1]) || !IsFiniteFloat(playerPos[2]))
		{
			return false;
		}

		if (!IsNonZeroFiniteVector(playerDir))
		{
			return false;
		}

		VectorToArray(playerPos, camera.pos);
		VectorToArray(playerDir, camera.dir);
		camera.ok = true;
		camera.viewport_moved = false;
		camera.view = "vehicle";
		camera.error = "";
		return true;
	}

	protected bool RequestedCameraPosition(MCPArgs args, out vector requested)
	{
		MCPCameraValidation validation = ValidateCameraArgs(args);
		if (!validation || !validation.ok)
		{
			return false;
		}

		requested = validation.pos;
		return IsFiniteFloat(requested[0]) && IsFiniteFloat(requested[1]) && IsFiniteFloat(requested[2]);
	}

	protected bool CameraPositionsMatch(vector observed, vector requested)
	{
		float dx = observed[0] - requested[0];
		float dy = observed[1] - requested[1];
		float dz = observed[2] - requested[2];
		float eps = CAMERA_SEATED_POSE_EPS_M;
		return (dx * dx) + (dy * dy) + (dz * dz) <= (eps * eps);
	}

	// Apply vs observe: after seated camera_set, compare the observed
	// GetCurrentCameraTransform to the requested pose. Matching pose is the
	// only scripted PASS. An unmoved cabin stays view=vehicle and fails.
	protected void ObserveSeatedCameraApply(MCPCamera camera, MCPArgs args)
	{
		if (!camera || camera.view != "vehicle")
		{
			return;
		}

		vector requested;
		if (!RequestedCameraPosition(args, requested) || !camera.pos || camera.pos.Count() < 3)
		{
			camera.ok = false;
			camera.viewport_moved = false;
			camera.view = "vehicle";
			camera.error = "camera_unmoved_cabin";
			return;
		}

		vector observed = Vector(camera.pos.Get(0), camera.pos.Get(1), camera.pos.Get(2));
		if (CameraPositionsMatch(observed, requested))
		{
			camera.ok = true;
			camera.view = "scripted";
			camera.viewport_moved = true;
			camera.error = "";
			return;
		}

		camera.ok = false;
		camera.viewport_moved = false;
		camera.view = "vehicle";
		camera.error = "camera_unmoved_cabin";
	}

	protected MCPCamera BuildCameraResult(string mode)
	{
		MCPCamera camera = new MCPCamera();
		camera.applied_mode = mode;

		// Shared by camera_get and the camera_set report (outside Dispatch).
		// Rejected snapshots keep pos/matrix/dir empty (ctor-initialized).
		string cameraError = CameraReadError();
		PlayerBase seatedPlayer = PlayerBase.Cast(GetGame().GetPlayer());
		if (ResolveLiveSeatedTransport(seatedPlayer))
		{
			if (FillSeatedCameraView(camera, seatedPlayer))
			{
				return camera;
			}

			camera.ok = false;
			camera.viewport_moved = false;
			camera.view = "";
			camera.error = cameraError;
			return camera;
		}

		if (CameraErrorIsMissingScripted(cameraError))
		{
			PlayerBase liberatedPlayer = PlayerBase.Cast(GetGame().GetPlayer());
			if (FillPlayerCameraView(camera, liberatedPlayer))
			{
				return camera;
			}

			camera.ok = false;
			camera.viewport_moved = false;
			camera.view = "";
			camera.error = "camera_illegible_player_transform";
			return camera;
		}

		if (cameraError != "")
		{
			camera.ok = false;
			camera.viewport_moved = false;
			camera.view = "";
			camera.error = cameraError;
			return camera;
		}

		camera.ok = true;
		camera.view = "scripted";
		Camera current = m_ActiveCam;
		vector matrix[4];
		current.GetTransform(matrix);
		MatrixToArray(matrix, camera.matrix);
		VectorToArray(current.GetWorldPosition(), camera.pos);
		VectorToArray(matrix[2], camera.dir);
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

	// fb-20260822-191204-46b3: the only bridge URL is "http://127.0.0.1:<port>/",
	// the form the daemon and both installers write. It is the RestContext base
	// that "poll?..." and "result?..." are appended to, so the final "/" belongs
	// to it (see PollContextUrl). <port> is 1 to 5 ASCII digits (ToAscii
	// 48..57) worth 1..65535. Anything else, userinfo, path, query, fragment,
	// whitespace or backslash included, is refused. The same rule is in
	// MCPBridge.c.
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

	protected void PostWeaponActionJob(MCPJob job, string forcedError)
	{
		MCPResult result;
		string verb;
		if (!job)
		{
			return;
		}
		verb = "";
		if (job.weapon_action)
		{
			verb = job.weapon_action.verb;
		}
		MCPWeaponControl.FinishWatch(verb, job.generation);
		result = new MCPResult();
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		result.weapon_action = job.weapon_action;
		if (forcedError != "")
		{
			result.ok = false;
			result.error = forcedError;
			PostResult(result);
			return;
		}
		if (job.error != "")
		{
			result.ok = false;
			result.error = job.error;
			PostResult(result);
			return;
		}
		result.ok = true;
		if (verb == "weapon_fire")
		{
			if (job.weapon_action)
			{
				result.accepted = job.weapon_action.accepted;
				result.error = job.weapon_action.reason;
			}
		}
		PostResult(result);
	}

	protected void PostInputTriggerJob(MCPJob job)
	{
		MCPResult result;
		if (!job)
		{
			return;
		}
		result = new MCPResult();
		result.id = job.id;
		result.tick_poll_sent = job.tick_poll_sent;
		result.tick_poll_callback = job.tick_poll_callback;
		result.tick_dispatch = job.tick_dispatch;
		result.input_trigger = job.input_trigger;
		if (job.error != "")
		{
			result.ok = false;
			result.error = job.error;
		}
		else
		{
			result.ok = true;
		}
		PostResult(result);
	}

	// The click or hold outlived its scheduled release by
	// INPUT_TRIGGER_JOB_SLACK_S: release the key now if this job still holds
	// it, then answer aborted with what released it.
	protected void PostInputTriggerTimeout(MCPJob job)
	{
		if (MCPInputTriggerControl.IsHeld() && MCPInputTriggerControl.Generation() == job.generation)
		{
			MCPInputTriggerControl.ReleaseAll("ttl");
		}
		if (job.input_trigger && MCPInputTriggerControl.WasReleased(job.generation))
		{
			FillInputTriggerRelease(job.input_trigger);
		}
		job.error = "aborted";
		PostInputTriggerJob(job);
	}

	protected void RestoreGameplay()
	{
		// Drops every override this bridge armed. Same method for the
		// restore_gameplay command, vehicle get-in cleanup and shutdown.
		MCPWeaponControl.ReleaseAll("cleared");
		// A held input_trigger key too. Shutdown releases first, as shutdown.
		MCPInputTriggerControl.ReleaseAll("restore");
		// Destructor cleanup can outlive CGame, whose destructor nulls g_Game.
		// Latched: this method has eight call sites and must not log per call.
		// Log reaches only Print, which needs no CGame, so the line survives the
		// teardown it reports.
		if (!GetGame())
		{
			if (!m_RestoreNoGameLogged)
			{
				m_RestoreNoGameLogged = true;
				Log("restore skipped: no game");
			}
			return;
		}

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		// f47b: while a camera handoff runs, the simulation stays off until
		// FinishCameraHandoff, even when a second restore lands meanwhile.
		if (player && m_PlayerSimulationDisabled && !m_CameraHandoffPending)
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

		ReleaseGameFocus();
	}

	// RestoreGameplay covers simulation, controls and HUD and never the
	// camera, so the restore_gameplay command answered ok:1 with the view still
	// locked to the debug camera -- and camera_set has no off mode, so reconnecting
	// was the only way out. Shutdown already performed the full teardown; sharing
	// it is what stops the two paths drifting apart again.
	// Ownership is the subtlety: FreeDebugCamera is a singleton the bridge does not
	// own and is only deactivated, while the staticcamera built for
	// orient/lookat/matrix is owned and must also be deleted.
	// f47b: an active staticcamera is not dropped straight back to the player
	// view, which froze the render; BeginCameraHandoff passes the view through the
	// free camera first and OnTick finishes the release. Shutdown has no later
	// tick, so it still tears down at once.
	protected void ReleaseCamera()
	{
		if (!m_Shutdown)
		{
			// A release already handing off owns the free camera until it finishes.
			if (m_CameraHandoffPending)
			{
				return;
			}

			if (m_ActiveCam && m_ActiveCamOwned && m_ActiveCam.IsActive())
			{
				if (BeginCameraHandoff())
				{
					return;
				}
			}
		}

		FinishCameraHandoff();

		if (m_ActiveCam)
		{
			m_ActiveCam.SetActive(false);
		}

		// FreeDebugCamera is a singleton the bridge does not own. camera_set
		// free can leave it active after m_ActiveCam is cleared (f5a7-3 trap).
		FreeDebugCamera freeCam = FreeDebugCamera.GetInstance();
		if (freeCam)
		{
			freeCam.SetActive(false);
		}

		DeleteOwnedCamera();
	}

	// Windowed diag clients capture the OS mouse on join (f298). Resetting
	// game focus returns the cursor to the desktop without touching OS input.
	void ReleaseGameFocus()
	{
		if (!GetGame())
		{
			return;
		}

		Input input = GetGame().GetInput();
		if (!input)
		{
			return;
		}

		input.ResetGameFocus();
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

	// f47b: an active staticcamera dropped straight back to the player view left
	// the client render frozen on its last frame while camera_get read player,
	// and keeping the camera undeleted did not help (run 24cf553a); releasing
	// after camera_set free renders live. Vanilla CameraToolsMenu leaves its
	// staticcameras by activating FreeDebugCamera (cameratoolsmenu.c:506-508) and
	// the free camera is left later with SetActive(false)
	// (developerfreecamera.c:52-74). So: static off, free on at the same pose,
	// player simulation off as ApplyFreeCamera does; FinishCameraHandoff undoes it
	// CAMERA_HANDOFF_TICKS OnTick calls later. False when there is no free camera.
	protected bool BeginCameraHandoff()
	{
		FreeDebugCamera freeCam = FreeDebugCamera.GetInstance();
		if (!freeCam)
		{
			return false;
		}

		vector handoffPos = m_ActiveCam.GetPosition();
		vector handoffOri = m_ActiveCam.GetOrientation();
		m_ActiveCam.SetActive(false);
		freeCam.SetPosition(handoffPos);
		freeCam.SetOrientation(handoffOri);
		freeCam.SetActive(true);

		PlayerBase player = PlayerBase.Cast(GetGame().GetPlayer());
		if (player)
		{
			player.DisableSimulation(true);
			m_PlayerSimulationDisabled = true;
		}

		m_CameraHandoffCam = m_ActiveCam;
		m_CameraHandoffTicks = 0;
		m_CameraHandoffPending = true;
		// The free camera is what the client shows until the handoff finishes.
		m_ActiveCam = freeCam;
		m_ActiveCamOwned = false;
		return true;
	}

	protected void TickCameraHandoff()
	{
		if (!m_CameraHandoffPending)
		{
			return;
		}

		m_CameraHandoffTicks = m_CameraHandoffTicks + 1;
		if (m_CameraHandoffTicks >= CAMERA_HANDOFF_TICKS)
		{
			FinishCameraHandoff();
		}
	}

	// The release measured live after camera_set free: the free camera goes, the
	// player simulation comes back, then the deactivated staticcamera is deleted.
	// Runs from OnTick, from a camera_set apply that comes first, and at shutdown.
	protected void FinishCameraHandoff()
	{
		if (!m_CameraHandoffPending)
		{
			return;
		}

		m_CameraHandoffPending = false;
		m_CameraHandoffTicks = 0;

		FreeDebugCamera freeCam = FreeDebugCamera.GetInstance();
		if (freeCam)
		{
			freeCam.SetActive(false);
		}
		m_ActiveCam = null;
		m_ActiveCamOwned = false;

		// Shutdown can run after CGame is gone (see RestoreGameplay).
		PlayerBase player;
		if (GetGame())
		{
			player = PlayerBase.Cast(GetGame().GetPlayer());
		}
		if (player && m_PlayerSimulationDisabled)
		{
			player.DisableSimulation(false);
			m_PlayerSimulationDisabled = false;
		}

		if (m_CameraHandoffCam)
		{
			g_Game.ObjectDelete(m_CameraHandoffCam);
		}
		m_CameraHandoffCam = null;
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
		// ShutdownInstance calls here, then releasing m_Instance runs our destructor.
		// The two lines are the only in-engine evidence this guard fired: the first
		// entry alone means no re-entry happened, both mean the second was refused.
		if (m_Shutdown)
		{
			if (!m_ShutdownReentryLogged)
			{
				m_ShutdownReentryLogged = true;
				Log("shutdown re-entered");
			}
			return;
		}
		m_Shutdown = true;
		Log("shutdown first entry");
		if (m_Dialog && m_Dialog.IsOpen())
		{
			m_Dialog.FinishDisconnected();
		}

		// MissionGameplay.c only shuts down from its destructor; OnMissionStart /
		// OnUpdate own the ticks. Our destructor may call Shutdown again (guard above).
		// Keep m_Configured until the first terminal POST. reset() clears pending REST
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
		MCPAnimTimeline.Abort("shutdown");
		MCPCarDrive.Clear();
		// Before RestoreGameplay, so the held key's release names shutdown.
		MCPInputTriggerControl.ReleaseAll("shutdown");
		RestoreGameplay();
		// m_Shutdown is already set, so this finishes any camera handoff and
		// deactivates and deletes at once (f47b).
		ReleaseCamera();

		// A completed cached callback is absent from m_PollCallbackRefs.
		if (m_PollCallback)
		{
			m_PollCallback.DetachBridge();
		}

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
